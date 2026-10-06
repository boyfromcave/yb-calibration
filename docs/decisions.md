# Decision log

One entry per decision that shapes results or contracts. Newest last. Format: id, date, owner,
decision, reason, consequences.

## Index

Every entry below, in log order (newest last). Integrator notes record how a request or
contract change from a work package was resolved; they supersede the entry they name.

| ID | Owner | Decision |
|---|---|---|
| [D-1](#d-1-2026-10-03-wp-0--locked--excluded-vocabulary-mapping) | WP-0 | "locked" / "excluded" vocabulary mapping |
| [D-2](#d-2-2026-10-03-wp-0--abandonblocks-is-locked) | WP-0 | `abandonBlocks` is locked |
| [D-3](#d-3-2026-10-03-wp-0--attestmaxage-is-derived-from-k-but-locked) | WP-0 | `attestMaxAge` is derived from `k` but locked |
| [D-4](#d-4-2026-10-03-wp-0--volperiodsperyear-derivation-is-network-aware) | WP-0 | `volPeriodsPerYear` derivation is network-aware |
| [D-5](#d-5-2026-10-03-wp-0--regtest-column-and-scale-dependent-invariants) | WP-0 | regtest column and scale-dependent invariants |
| [D-6](#d-6-2026-10-03-wp-0--default_ref_lag-is-excluded-other-header-constants-are-constants) | WP-0 | `DEFAULT_REF_LAG` is excluded, other header constants are constants |
| [D-7](#d-7-2026-10-03-wp-0--policy-dependent-invariants-and-defaults) | WP-0 | policy-dependent invariants and defaults |
| [D-8](#d-8-2026-10-03-wp-0--study--recommend--report-cli-owned-by-wp-8) | WP-0 | `study` / `recommend` / `report` CLI owned by WP-8 |
| [D-WP6-1](#d-wp6-1-2026-10-03-wp-6--per-candidate-rng-common-random-numbers-by-default) | WP-6 | per-candidate RNG: common random numbers by default |
| [D-WP6-2](#d-wp6-2-2026-10-03-wp-6--search-axes-are-anchored-on-the-current-value) | WP-6 | search axes are anchored on the current value |
| [D-WP6-3](#d-wp6-3-2026-10-03-wp-6--successive-halving-over-monte-carlo-paths-hyperband-lite) | WP-6 | successive halving over Monte-Carlo paths ("Hyperband-lite") |
| [D-WP6-4](#d-wp6-4-2026-10-03-wp-6--rejected-candidates-are-counted-base-is-always-evaluated) | WP-6 | rejected candidates are counted, base is always evaluated |
| [D-WP6-5](#d-wp6-5-2026-10-03-wp-6--robust-selection-and-tie-breaking) | WP-6 | robust selection and tie-breaking |
| [D-WP6-6](#d-wp6-6-2026-10-03-wp-6--sensitivity-estimators) | WP-6 | sensitivity estimators |
| [D-WP6-7](#d-wp6-7-2026-10-03-wp-6--ybcal-sensitivity-cli-deferred) | WP-6 | `ybcal sensitivity` CLI deferred (resolved by D-WP8-8) |
| [D-WP1-1](#d-wp1-1-2026-10-03-wp-1--how-the-reference-model-is-vendored) | WP-1 | how the reference model is vendored |
| [D-WP1-2](#d-wp1-2-2026-10-03-wp-1--kernels-follow-the-c-where-the-reference-model-differs) | WP-1 | kernels follow the C++ where the reference model differs |
| [D-WP1-3](#d-wp1-3-2026-10-03-wp-1--undefined-encodings) | WP-1 | undefined encodings |
| [D-WP1-4](#d-wp1-4-2026-10-03-wp-1--rolling-medians-by-wavelet-matrix) | WP-1 | rolling medians by wavelet matrix |
| [D-WP1-5](#d-wp1-5-2026-10-03-wp-1--reference-models-stale-mainnet-abandon_blocks) | WP-1 | reference model's stale mainnet `abandon_blocks` |
| [D-WP2-1](#d-wp2-1-2026-10-03-wp-2--pricepath-stays-in-ybcaltypes-helpers-in-ybcaldatapricepath) | WP-2 | `PricePath` stays in `ybcal.types`; helpers in `ybcal.data.pricepath` |
| [D-WP2-2](#d-wp2-2-2026-10-03-wp-2--loaders-last-duplicate-wins-as-of-resampling-with-a-filled-mask) | WP-2 | loaders: last duplicate wins, as-of resampling with a filled mask |
| [D-WP2-3](#d-wp2-3-2026-10-03-wp-2--fetch-chunked-hourly-history-explicit-network-blocked-error) | WP-2 | fetch: chunked hourly history, explicit network-blocked error |
| [D-WP2-4](#d-wp2-4-2026-10-03-wp-2--synthetic-presets-are-placeholders-fitting-methods) | WP-2 | synthetic presets are placeholders; fitting methods |
| [D-WP2-5](#d-wp2-5-2026-10-03-wp-2--scenario-bases-are-centred-families-via-variants) | WP-2 | scenario bases are centred; families via `[[variants]]` |
| [D-WP2-6](#d-wp2-6-2026-10-03-wp-2----kind-hashrate-is-the-pool-share-csv) | WP-2 | `--kind hashrate` is the pool-share CSV |
| [D-WP9-1](#d-wp9-1-2026-10-03-wp-9--time-scaling-rules) | WP-9 | time-scaling rules |
| [D-WP9-2](#d-wp9-2-2026-10-03-wp-9--bondmin-on-a-scaled-set-is-the-regtest-10-yec) | WP-9 | `bondMin` on a scaled set is the regtest 10 YEC |
| [D-WP9-3](#d-wp9-3-2026-10-03-wp-9--contract-note-the-sunset-invariant-at-regtest-scale) | WP-9 | contract note: the `sunset` invariant at regtest scale (resolved below) |
| [D-WP9-4](#d-wp9-4-2026-10-03-wp-9--own-minimal-launcher-beside-yellowback-devnet) | WP-9 | own minimal launcher beside `yellowback-devnet` |
| [D-WP9-5](#d-wp9-5-2026-10-03-wp-9--differential-contract-and-pass-criterion) | WP-9 | differential contract and pass criterion (simulator delivered, D-WP3-7) |
| [D-WP9-6](#d-wp9-6-2026-10-03-wp-9--version-skew-policy) | WP-9 | version-skew policy |
| [note](#d-wp9-3-resolution-2026-10-03-integrator) | integrator | D-WP9-3 resolution |
| [D-WP3-1](#d-wp3-1-2026-10-03-wp-3--engine-stages-two-hook-only-additions) | WP-3 | engine stages: two hook-only additions |
| [D-WP3-2](#d-wp3-2-2026-10-03-wp-3--internal-activation-is-exact-not-always-active) | WP-3 | internal activation is exact, not "always ACTIVE" |
| [D-WP3-3](#d-wp3-3-2026-10-03-wp-3--pin-1-lives-in-the-engine-pinned-keys-are-pool-ids) | WP-3 | PIN-1 lives in the engine; pinned keys are pool ids |
| [D-WP3-4](#d-wp3-4-2026-10-03-wp-3--ycash-subsidy-schedule-and-issuedzat-origin) | WP-3 | Ycash subsidy schedule and issuedZat origin |
| [D-WP3-5](#d-wp3-5-2026-10-03-wp-3--hour-mode-kernel-tolerance-request-to-wp-0wp-8) | WP-3 | hour-mode kernel tolerance (request to WP-0/WP-8; resolved below) |
| [D-WP3-6](#d-wp3-6-2026-10-03-wp-3--oracle-model-choices) | WP-3 | oracle model choices |
| [D-WP3-7](#d-wp3-7-2026-10-03-wp-3--simulate_devnet-and-a-wp-9-test-adjustment) | WP-3 | `simulate_devnet` and a WP-9 test adjustment |
| [note](#d-wp3-5-resolution-2026-10-03-integrator) | integrator | D-WP3-5 resolution |
| [D-WP5-1](#d-wp5-1-2026-10-03-wp-5--attestation-walk-segments-of-constant-status--sparse-points) | WP-5 | attestation walk: segments of constant status + sparse points |
| [D-WP5-2](#d-wp5-2-2026-10-03-wp-5--attestor-behaviour-and-bundle-assembly-model) | WP-5 | attestor behaviour and bundle assembly model |
| [D-WP5-3](#d-wp5-3-2026-10-03-wp-5--pin-coupling-with-the-oracle-engine) | WP-5 | PIN coupling with the oracle engine (item 2 done in D-WP8-6) |
| [D-WP5-4](#d-wp5-4-2026-10-03-wp-5--mid-chain-starts-and-height-frames) | WP-5 | mid-chain starts and height frames |
| [D-WP5-5](#d-wp5-5-2026-10-03-wp-5--g5-analytic-estimators) | WP-5 | G5 analytic estimators |
| [D-WP5-6](#d-wp5-6-2026-10-03-wp-5--g8-analytic-estimators) | WP-5 | G8 analytic estimators |
| [note](#integration-of-wp-3-and-wp-5-2026-10-03-integrator) | integrator | Integration of WP-3 and WP-5 |
| [D-WP7a-1](#d-wp7a-1-2026-10-03-wp-7a--g1-objective-is-normalised-by-the-current-windows) | WP-7a | G1 objective is normalised by the current windows |
| [D-WP7a-2](#d-wp7a-2-2026-10-03-wp-7a--g1-manipulation-constraint-analytic-v16-plus-a-simulated-check) | WP-7a | G1 manipulation constraint: analytic V16 plus a simulated check |
| [D-WP7a-3](#d-wp7a-3-2026-10-03-wp-7a--g1g2-pool-outage-model-and-no_price-availability) | WP-7a | G1/G2 pool-outage model and NO_PRICE availability |
| [D-WP7a-4](#d-wp7a-4-2026-10-03-wp-7a--g1-keeps-halt-3-working-recall-constraint-on-crash-70-1d) | WP-7a | G1 keeps HALT-3 working: recall constraint on crash-70-1d |
| [D-WP7a-5](#d-wp7a-5-2026-10-03-wp-7a--g2-window-rule-also-bounds-the-k12-trap-responsiveness-on-σ) | WP-7a | G2 window rule also bounds the K12 trap; responsiveness on σ̂ |
| [D-WP7a-6](#d-wp7a-6-2026-10-03-wp-7a--sigmarefbps-round-down-keep-inside-the-m14-band) | WP-7a | sigmaRefBps: round down; KEEP inside the M14 band |
| [D-WP7a-7](#d-wp7a-7-2026-10-03-wp-7a--evidence-directory-and-shared-realisations) | WP-7a | evidence directory and shared realisations |
| [note](#contract-changes-from-wp-7a-2026-10-03-integrator) | integrator | Contract changes from WP-7a |
| [D-WP7c-1](#d-wp7c-1-2026-10-03-wp-7c--g5g8-as-families-of-one-at-a-time-rules) | WP-7c | G5/G8 as families of one-at-a-time rules |
| [D-WP7c-2](#d-wp7c-2-2026-10-03-wp-7c--judgement-constants-requested-policy-keys) | WP-7c | judgement constants; requested policy keys |
| [D-WP7c-3](#d-wp7c-3-2026-10-03-wp-7c--g5-objective-false-halts-detection-as-a-constraint) | WP-7c | G5 objective: false halts, detection as a constraint |
| [D-WP7c-4](#d-wp7c-4-2026-10-03-wp-7c--g8-capture-is-evaluated-at-bundle-level) | WP-7c | G8 capture is evaluated at bundle level |
| [D-WP7c-5](#d-wp7c-5-2026-10-03-wp-7c--ported-spreadspypinratepy-the-200300-reading) | WP-7c | ported spreads.py/pinrate.py; the "200–300" reading |
| [D-WP7c-6](#d-wp7c-6-2026-10-03-wp-7c--attestsimulate-notes-found-while-confirming-g8) | WP-7c | attest.simulate notes found while confirming G8 |
| [note](#contract-changes-from-wp-7c-2026-10-03-integrator) | integrator | Contract changes from WP-7c |
| [D-WP4-1](#d-wp4-1-2026-10-03-wp-4--vault-book--vectorised-lifecycle-plan--exact-sequential-pass) | WP-4 | vault book = vectorised lifecycle plan + exact sequential pass |
| [D-WP4-2](#d-wp4-2-2026-10-03-wp-4--wallet-preflight-refusals-and-voids-order-inside-a-step) | WP-4 | wallet preflight, refusals and VOIDs; order inside a step |
| [D-WP4-3](#d-wp4-3-2026-10-03-wp-4--personas) | WP-4 | personas (policy keys: resolution below) |
| [D-WP4-4](#d-wp4-4-2026-10-03-wp-4--hour-mode-conventions-and-the-default-kernel) | WP-4 | hour-mode conventions and the default kernel |
| [D-WP4-5](#d-wp4-5-2026-10-03-wp-4--bad-debt-definitions) | WP-4 | bad-debt definitions |
| [D-WP4-6](#d-wp4-6-2026-10-03-wp-4--findings-for-the-owner-design-notes-not-parameter-changes) | WP-4 | findings for the owner (design notes, not parameter changes) |
| [D-WP4-7](#d-wp4-7-2026-10-03-wp-4--contract-notes) | WP-4 | contract notes |
| [note](#d-wp4-3-resolution-2026-10-03-integrator) | integrator | D-WP4-3 resolution |
| [D-WP7b-1](#d-wp7b-1-2026-10-03-wp-7b--g3g4-share-one-hour-ensemble-pbad-debt-is-wp-4s-fast-path) | WP-7b | G3/G4 share one hour ensemble; P(bad debt) is WP-4's fast path |
| [D-WP7b-2](#d-wp7b-2-2026-10-03-wp-7b--g3-ratio-rule-materiality-on-the-ratio-blocked--least-violating) | WP-7b | G3 ratio rule: materiality on the ratio; BLOCKED → least violating |
| [D-WP7b-3](#d-wp7b-3-2026-10-03-wp-7b--class-boundaries-are-verified-heterogeneity-is-a-design-note) | WP-7b | class boundaries are verified; heterogeneity is a design note |
| [D-WP7b-4](#d-wp7b-4-2026-10-03-wp-7b--g4-grace-the-debt-side-enters-through-j-only) | WP-7b | G4 grace: the debt side enters through J only |
| [D-WP7b-5](#d-wp7b-5-2026-10-03-wp-7b--g9-rules-are-admissible-ranges-with-a-verify-rule) | WP-7b | G9 rules are admissible ranges with a verify rule |
| [D-WP7b-6](#d-wp7b-6-2026-10-03-wp-7b--release-study-inputs-and-rounding) | WP-7b | release study inputs and rounding |
| [D-WP7b-7](#d-wp7b-7-2026-10-03-wp-7b--design-note-format-and-requested-policy-keys) | WP-7b | design-note format and requested policy keys |
| [note](#contract-changes-from-wp-7b-2026-10-03-integrator) | integrator | Contract changes from WP-7b |
| [D-WP7d-1](#d-wp7d-1-2026-10-03-wp-7d--g6-judgement-model-and-rule-readings) | WP-7d | G6 judgement model and rule readings |
| [D-WP7d-2](#d-wp7d-2-2026-10-03-wp-7d--g6-fee-model-and-the-blocked-fallback) | WP-7d | G6 fee model and the BLOCKED fallback |
| [D-WP7d-3](#d-wp7d-3-2026-10-03-wp-7d--affordability-system-tolerance-halt-2-horizon) | WP-7d | affordability, system tolerance, HALT-2 horizon |
| [D-WP7d-4](#d-wp7d-4-2026-10-03-wp-7d--supplycapbps-judged-on-cap-bound-liquidation-demand) | WP-7d | supplyCapBps judged on cap-bound liquidation demand |
| [D-WP7d-5](#d-wp7d-5-2026-10-03-wp-7d--halt-2-timeliness-and-halt-3-classification) | WP-7d | HALT-2 timeliness and HALT-3 classification |
| [D-WP7d-6](#d-wp7d-6-2026-10-03-wp-7d--requested-policy-keys) | WP-7d | requested policy keys |
| [D-WP7d-7](#d-wp7d-7-2026-10-03-wp-7d--excluded-l6-values-and-payeewindow) | WP-7d | excluded L6 values and payeeWindow |
| [note](#contract-changes-from-wp-7d-2026-10-03-integrator) | integrator | Contract changes from WP-7d |
| [D-WP8-1](#d-wp8-1-2026-10-03-wp-8--joint-pass-re-statement-against-the-shipped-set) | WP-8 | joint pass: re-statement against the shipped set |
| [D-WP8-2](#d-wp8-2-2026-10-03-wp-8--the-four-top-level-risk-metrics-fast-model) | WP-8 | the four top-level risk metrics (fast model) |
| [D-WP8-3](#d-wp8-3-2026-10-03-wp-8--insensitive-labels-it-does-not-override) | WP-8 | "insensitive" labels, it does not override |
| [D-WP8-4](#d-wp8-4-2026-10-03-wp-8--sensitivity-design-1-step-grouping-admissible-moves) | WP-8 | sensitivity design: ±1 step, grouping, admissible moves |
| [D-WP8-5](#d-wp8-5-2026-10-03-wp-8--patch-layout-and-the-vendored-paramscpp) | WP-8 | patch layout and the vendored params.cpp |
| [D-WP8-6](#d-wp8-6-2026-10-03-wp-8--pin-1pin-2-fixed-point-in-the-engine-d-wp5-3-item-2) | WP-8 | PIN-1/PIN-2 fixed point in the engine (D-WP5-3 item 2) |
| [D-WP8-7](#d-wp8-7-2026-10-03-wp-8--robust-selection-is-left-to-the-studies) | WP-8 | robust selection is left to the studies |
| [D-WP8-8](#d-wp8-8-2026-10-03-wp-8--cli-wiring) | WP-8 | CLI wiring |
| [D-WP8-9](#d-wp8-9-2026-10-03-wp-8--runtime-of-the-joint-pass) | WP-8 | runtime of the joint pass |
| [D-WP8-10](#d-wp8-10-2026-10-03-wp-8--rich-design-notes-blocked-rendering-late-round-failures) | WP-8 | rich design notes, BLOCKED rendering, late-round failures (G6 bug fixed in the last note) |
| [note](#g6-undefined-honest-p99-2026-10-03-integrator) | integrator | G6 undefined honest p99 |
| [D-WP10-1](#d-wp10-1-2026-10-03-wp-10--documentation-integration-policy-reference-is-tested) | WP-10 | documentation integration; policy reference is tested |
| [D-RD-D1](#d-rd-d1-2026-10-03-data--fitted-drift-is-neutralised-by-default) | data (M7) | fitted drift is neutralised by default |
| [D-RD-D2](#d-rd-d2-2026-10-03-data--long-history-sources-and-the-splice) | data (M7) | long-history sources and the splice |
| [D-RD-D3](#d-rd-d3-2026-10-03-data--reconstructed-spreads-stand-in-for-the-two-week-log) | data (M7) | reconstructed spreads stand in for the two-week log |
| [D-RD-D4](#d-rd-d4-2026-10-03-data--stale-runs-regime-embedding-and-garch-at-its-bounds) | data (M7) | stale runs, regime embedding and GARCH at its bounds |
| [D-RD-D5](#d-rd-d5-2026-10-03-data--data-informed-policy-keys) | data (M7) | data-informed policy keys |
| [D-RD-AUD-1](#d-rd-aud-1-2026-10-03-audit--one-drift-convention-for-solvency-ensembles-centred) | audit | one drift convention for solvency ensembles (centred) |
| [D-RD-AUD-2](#d-rd-aud-2-2026-10-03-audit--the-summary-quotes-g3s-pbad-debt-not-the-fast-model) | audit | the summary quotes G3's P(bad debt), not the fast model |
| [D-RD-AUD-3](#d-rd-aud-3-2026-10-03-audit--pbad-debt-at-claim-opening-stays-the-constraint-severity-and-reach-are-evidence) | audit | P(bad debt) at claim opening stays the constraint; severity and reach are evidence |
| [D-RD-AUD-4](#d-rd-aud-4-2026-10-03-audit--a-red-4b-closure-counts-only-when-the-exit-pays) | audit | a RED-4(b) closure counts only when the exit pays |
| [D-RD-AUD-5](#d-rd-aud-5-2026-10-03-audit--supplycapbps-moves-only-for-admitted-demand-and-an-evidenced-depth-bound) | audit | `supplyCapBps` moves only for admitted demand and an evidenced depth bound |
| [D-RD-AUD-6](#d-rd-aud-6-2026-10-03-audit--halt-3-calm-hours-share-the-availability-budget) | audit | HALT-3 calm hours share the availability budget |
| [D-RD-AUD-7](#d-rd-aud-7-2026-10-03-audit--a-blocked-sigmamultmaxbps-reports-the-bound-and-what-drives-the-need) | audit | a BLOCKED `sigmaMultMaxBps` reports the bound and what drives the need |
| [D-RD-AUD-8](#d-rd-aud-8-2026-10-03-audit--peermin-must-hold-at-the-participation-floor) | audit | `peerMin` must hold at the participation floor |
| [D-RD-AUD-9](#d-rd-aud-9-2026-10-03-audit--forced-moves-are-minimal-g1-reads-real-pool-shares) | audit | forced moves are minimal; G1 reads real pool shares |
| [D-RD-AUD-10](#d-rd-aud-10-2026-10-03-audit--heterogeneity-is-not-a-constraint-neighbours-name-the-rules-own-constraints) | audit | heterogeneity is not a constraint; neighbours name the rule's own constraints |
| [D-RD-AUD-11](#d-rd-aud-11-2026-10-03-audit--volstep--pfastwindow--2) | audit | `volStep ≥ pFastWindow / 2` |
| [D-RD-AUD-12](#d-rd-aud-12-2026-10-03-audit--quick-budget-verdicts-are-not-lock-grade-seed-and-policy-robustness) | audit | quick-budget verdicts are not lock-grade: seed and policy robustness |
| [D-RD-DEV-1](#d-rd-dev-1-2026-10-03-devnet--the-devnet-on-a-shared-machine-port-band-reuse-of-a-built-tree-teardown) | devnet (M6) | the devnet on a shared machine: port band, reuse of a built tree, teardown |
| [D-RD-DEV-2](#d-rd-dev-2-2026-10-03-devnet--miners-follow-one-plan-across-the-schedule-scenario-realism) | devnet (M6) | miners follow one plan across the schedule (scenario realism) |
| [D-RD-DEV-3](#d-rd-dev-3-2026-10-03-devnet--vault-differential-replay-the-nodes-transactions-through-the-rule-layer) | devnet (M6) | vault differential: replay the node's transactions through the rule layer |
| [D-RD-DEV-4](#d-rd-dev-4-2026-10-03-devnet--emulated-attestor-seats-instead-of-the-rust-agent) | devnet (M6) | emulated attestor seats instead of the Rust agent |
| [D-RD-DEV-5](#d-rd-dev-5-2026-10-03-devnet--two-simulator-fixes-the-devnet-found-attestation-frame-dormancy-record) | devnet (M6) | two simulator fixes the devnet found (attestation frame, dormancy record) |
| [D-RD-DEV-6](#d-rd-dev-6-2026-10-03-devnet--binaries-and-skew-for-the-2026-10-validation) | devnet (M6) | binaries and skew for the 2026-10 validation |
| [D-RD-DEV-7](#d-rd-dev-7-2026-10-03-devnet--what-remains-unmodelled-and-which-studies-it-weakens) | devnet (M6) | what remains unmodelled, and which studies it weakens |
| [D-RD-INF-1](#d-rd-inf-1-2026-10-03-infra--price-inputs-by-role-observed-to-observed-returns-everywhere) | infra (wave 2) | price inputs by role; observed-to-observed returns everywhere |
| [D-RD-INF-2](#d-rd-inf-2-2026-10-04-infra--owner-pinned-parameters-are-studied-and-kept) | infra (wave 2) | owner-pinned parameters are studied and kept |
| [D-RD-INF-3](#d-rd-inf-3-2026-10-04-infra--policy-unmeetable-in-this-environment-is-not-blocked) | infra (wave 2) | "policy unmeetable in this environment" is not BLOCKED |
| [D-RD-INF-4](#d-rd-inf-4-2026-10-04-infra--g9-on-real-data-no-zero-amounts-thin-market-and-majority-pool-are-environment-limits) | infra (wave 2) | G9 on real data: no zero amounts; thin market and majority pool are environment limits |
| [D-RD-INF-5](#d-rd-inf-5-2026-10-04-infra--the-robustness-harness-windows-price-models-policy-overrides) | infra (wave 2) | the robustness harness: windows, price models, policy overrides |
| [D-RD-INF-6](#d-rd-inf-6-2026-10-04-infra--report-polish-blocked-reasons-name-the-value-lock-readiness-counts-pins-and-environment-limits) | infra (wave 2) | report polish: BLOCKED reasons name the value; lock-readiness counts pins and environment limits |
| [D-RD-COL-1](#d-rd-col-1-2026-10-04-collateral--long-horizon-members-come-from-the-daily-history) | collateral (wave 2) | long-horizon members come from the daily history |
| [D-RD-COL-2](#d-rd-col-2-2026-10-04-collateral--the-fork-launch-fall-is-a-sensitivity-not-the-base-window) | collateral (wave 2) | the fork-launch fall is a sensitivity, not the base window |
| [D-RD-COL-3](#d-rd-col-3-2026-10-04-collateral--a-member-must-reproduce-volatility-by-horizon) | collateral (wave 2) | a member must reproduce volatility by horizon |
| [D-RD-COL-4](#d-rd-col-4-2026-10-04-collateral--class-tolerances-bc-are-an-environment-limit-the-term-classes-as-shipped) | collateral (wave 2) | class tolerances B/C are an environment limit; the term classes as shipped |
| [D-RD-COL-5](#d-rd-col-5-2026-10-04-collateral--a-seller-meets-the-bid-side-of-the-book) | collateral (wave 2) | a seller meets the bid side of the book |
| [D-RD-COL-6](#d-rd-col-6-2026-10-04-collateral--the-claimant-is-a-yec-holder-nine-claims-in-ten-must-pay) | collateral (wave 2) | the claimant is a YEC holder; nine claims in ten must pay |
| [D-RD-COL-7](#d-rd-col-7-2026-10-04-collateral--emergencyratiobps-keep-and-on-real-crashes-it-does-not-matter) | collateral (wave 2) | emergencyRatioBps: KEEP, and on real crashes it does not matter |
| [D-RD-COL-8](#d-rd-col-8-2026-10-04-collateral--abandonblocks-the-90-d-was-an-artefact-w21-30-d-holds) | collateral (wave 2) | abandonBlocks: the "90 d" was an artefact; W21 30 d holds |
| [D-RD-COL-9](#d-rd-col-9-2026-10-04-collateral--the-w20w16-gate-and-the-ratios) | collateral (wave 2) | the W20/W16 gate and the ratios |
| [D-RD-ORA-1](#d-rd-ora-1-2026-10-04-oracle--the-oracle-quotes-on-the-real-pool-landscape) | oracle (wave 2) | the oracle quotes on the real pool landscape |
| [D-RD-ORA-2](#d-rd-ora-2-2026-10-04-oracle--g1-is-environment-limited-least-harm-is-pmid-1152) | oracle (wave 2) | G1 is environment-limited; least harm is pMid 1,152 |
| [D-RD-ORA-3](#d-rd-ora-3-2026-10-04-oracle--real-history-replay-is-evidence-on-every-g2--g7-candidate) | oracle (wave 2) | real-history replay is evidence on every G2 / G7 candidate |
| [D-RD-ORA-4](#d-rd-ora-4-2026-10-04-oracle--real-history-and-rogue-pool-scenarios) | oracle (wave 2) | real-history and rogue-pool scenarios |
| [D-RD-ORA-5](#d-rd-ora-5-2026-10-04-oracle--g2-on-real-data-daily-regime-fit-cap-need-on-the-real-history) | oracle (wave 2) | G2 on real data: daily regime fit; cap need on the real history |
| [D-RD-ORA-6](#d-rd-ora-6-2026-10-04-oracle--halt-3-availability-read-on-the-real-history) | oracle (wave 2) | HALT-3 availability read on the real history |
| [D-RD-ORA-7](#d-rd-ora-7-2026-10-04-oracle--globalratiohaltbps-is-owner-pinned-in-effect) | oracle (wave 2) | globalRatioHaltBps is owner-pinned in effect |
| [D-RD-ORA-8](#d-rd-ora-8-2026-10-04-oracle--final-values-for-g1-g2-and-g7s-halts-and-how-they-were-tested) | oracle (wave 2) | final values for G1, G2 and G7's halts, and how they were tested |
| [D-RD-ATT-1](#d-rd-att-1-2026-10-04-attestation--honest-pools-and-attestors-quote-what-the-shipped-agents-quote) | attestation (wave 2) | honest pools and attestors quote what the shipped agents quote |
| [D-RD-ATT-2](#d-rd-att-2-2026-10-04-attestation--the-sample-agent-configs-must-ship-min_sources--2) | attestation (wave 2) | the sample agent configs must ship `min_sources = 2` |
| [D-RD-ATT-3](#d-rd-att-3-2026-10-04-attestation--divergebpsattest-is-decided-on-pfast-vs-amint-not-on-venue-pairs) | attestation (wave 2) | `divergeBpsAttest` is decided on pFast vs aMint, not on venue pairs |
| [D-RD-ATT-4](#d-rd-att-4-2026-10-04-attestation--bondmin-priced-at-the-reference-price-seat-splitting-capture-security-over-one-set-lifetime) | attestation (wave 2) | `bondMin` priced at the reference price; seat-splitting capture; security over one set lifetime |
| [D-RD-ATT-5](#d-rd-att-5-2026-10-04-attestation--attestinterval-what-the-attestation-age-adds-to-mint-10) | attestation (wave 2) | `attestInterval`: what the attestation age adds to MINT-10 |
| [D-RD-ATT-6](#d-rd-att-6-2026-10-04-attestation--the-real-pool-landscape-as-scenarios) | attestation (wave 2) | the real pool landscape as scenarios |
| [D-RD-ATT-7](#d-rd-att-7-2026-10-04-attestation--npenalty-at-the-calm-steady-state) | attestation (wave 2) | `nPenalty` at the calm steady state |
| [D-RD-ATT-8](#d-rd-att-8-2026-10-04-attestation--fees-under-the-owner-pinned-afee-1-the-attestor-floor-is-an-environment-limit) | attestation (wave 2) | fees under the owner-pinned AFEE-1: the attestor floor is an environment limit |
| [D-RD-ATT-9](#d-rd-att-9-2026-10-04-attestation--attestor-capture-priced-in-money) | attestation (wave 2) | attestor capture priced in money |
| [D-RD-ATT-10](#d-rd-att-10-2026-10-04-attestation--robustness-runs-and-the-final-g6g8-values) | attestation (wave 2) | robustness runs and the final G6/G8 values |
| [D-RD-ATT-11](#d-rd-att-11-2026-10-04-attestation--qlowbps-stays-3333-griefing-resistance-is-not-bought-with-cheaper-theft) | attestation (wave 2) | `qLowBps` stays 3,333: griefing resistance is not bought with cheaper theft |
| [D-RD-ATT-12](#d-rd-att-12-2026-10-04-attestation--devnet-attestor-dormancy-scenario) | attestation (wave 2) | devnet `attestor-dormancy` scenario |
| [D-RD-FIN-1](#d-rd-fin-1-2026-10-05-integrator--the-october-2026-real-data-recommendation) | integrator | the October 2026 real-data recommendation |

## D-1 (2026-10-03, WP-0) — "locked" / "excluded" vocabulary mapping

**Decision.** The tool uses the owner's words and records how they map onto the plan's terms
(PLAN §1.2):

| ybcal class | Plan / spec term | Change path |
|---|---|---|
| `locked` | consensus-shaped among enforcing miners (v2 K10, spec §3.1; v3 delta) | new parameter set keyed by start height, at/after the previous sunset (L8) or after a full ENFORCEMENT-halted signal window (W19); a sunset-only change is a renewal (W18) |
| `excluded` | rows marked informational, wallet default (L6), wallet/agent policy, node-local | patch release |
| `per-release` | `startHeight`, `enforceUntilHeight` (M14, L8) | every release, from the tip |
| `constant` | protocol constants (`params.h`) | verified, never tuned |
| `derived` | fixed by a formula from a parent | follows the parent; locked when `consensus` is true |
| `meta` | `network`, `addressVersion` (D10) | not calibrated |

**Consequence.** `ParamSpec.change_path` turns this into the report's locked / patch-release note.

## D-2 (2026-10-03, WP-0) — `abandonBlocks` is locked

**Decision.** Classified `locked` (PLAN §1.3), not node-local.
**Reason.** The spec does not mark the row informational or wallet default, so K10's wording makes
it consensus-shaped; it gates TPL-1/2, MP-1 and the `yed_sweep` abandonment predicate, which every
release must answer alike (spec `ABANDON_BLOCKS` row, W21).
**Consequence.** G4 recommendations for it carry the locked note. The W21 invariant
`abandonBlocks ≥ grace` and the plan's proposed `abandonBlocks ≥ runbook length` are both enforced
(margin at current values: 34,560 − (2,016 + 2,016 + 16,128 + 4,608 buffer) = 9,792 blocks).

## D-3 (2026-10-03, WP-0) — `attestMaxAge` is derived from `k` but locked

**Decision.** `attestMaxAge` has `klass="derived"`, `derive = 2 · attestInterval`,
`consensus=True` (change path `locked`), while its parent `attestInterval` (k) is `excluded`
(agent policy).
**Reason.** The spec marks k as agent policy but `ATTEST_MAX_AGE = 2·k` (R4) is read by BUNDLE-1 and
REV-1. `ParamSet.replace(attestInterval=…)` therefore moves a locked value.
**Consequence.** G8 must treat a change of k as a *locked* change whenever it moves `attestMaxAge`;
changing k alone (holding `attestMaxAge` explicitly) violates the `attest_max_age` invariant, so the
two always move together.

## D-4 (2026-10-03, WP-0) — `volPeriodsPerYear` derivation is network-aware

**Decision.** At mainnet scale `volPeriodsPerYear = BLOCKS_PER_YEAR / volStep` (exact division
required); on regtest it stays 8,760.
**Reason.** PLAN §1.3 lists it as derived (K13), but the spec says the annualisation is "an
independent parameter, deliberately equal on every network" — regtest uses `volStep = 8` with
8,760, so the formula only holds at mainnet scale.
**Consequence.** The `derive` callable receives the full value mapping (including `network`); the
`vol_periods` invariant checks the formula at mainnet scale and 8,760 at regtest scale. WP-9's
scaler must keep 8,760.

## D-5 (2026-10-03, WP-0) — regtest column and scale-dependent invariants

**Decision.** The registry's regtest column is `RegtestParams()` with the six flags at their
defaults: `startHeight=1` (the flag is required and must be positive), `sigmaRefBps=0`,
`supplyCapBps=0`, `enforceUntilHeight=0`, `attestArmMin=3` (the header default), `bundleCarrier=
SCRIPTSIG`. Invariants that encode mainnet block counts (`abandon_ge_runbook`, `release_lead`,
`bond_lock_year`, `regtest_zero_meanings`) are skipped for regtest-scale sets, and the `sunset`
invariant accepts `enforceUntilHeight = 0` only at regtest scale.
**Reason.** These are the regtest column's legitimate differences (spec §3.1: 0 = none / fixed
multiplier / never arms; regtest bonds lock 200 blocks).
**Consequence.** Both shipped columns pass every applicable invariant at the pin; no invariant was
weakened for mainnet.

## D-6 (2026-10-03, WP-0) — `DEFAULT_REF_LAG` is excluded, other header constants are constants

**Decision.** `DEFAULT_REF_LAG` (a `params.h` constant) is classified `excluded` (G9, wallet
default `-yellowbackmintlag`) per PLAN §1.3; `REF_WINDOW`, `TOKEN_VALUE`, `PRICE_MIN/MAX`,
`BLOCKS_PER_*` and `MAX_REF_LAG` are `constant`.
**Consequence.** G9 recommends `DEFAULT_REF_LAG` with a patch-release note.

## D-7 (2026-10-03, WP-0) — policy-dependent invariants and defaults

**Decision.** Two §1.4 checks depend on the policy: `qlow_vs_entity` uses
`max_single_entity_weight_share` (default 0.25 → 2,500 bps < qLow 3,333) and `fee_floor_mintable`
uses `worst_price_usd` (default $100 = `PRICE_MAX`: a $100 class-C minMint vault at 300 % needs 3 YEC
≥ 4·feeMin = 2 YEC). Without a policy, `Context()` skips the entity check and uses `PRICE_MAX`.
The runbook buffer defaults to 4,608 blocks (4 days) and `operator_upgrade_window_blocks` to 2,016
(so the current `activationDelay` meets it). These are starting tolerances for the owner (PLAN §12).

## D-8 (2026-10-03, WP-0) — `study` / `recommend` / `report` CLI owned by WP-8

**Decision.** The `ybcal study` driver (`ybcal.studies.cli.cli_study`) is assigned to WP-8 with
`recommend`, so the three parallel WP-7 agents do not collide on one file; WP-7 agents test their
studies through the library API.

## D-WP6-1 (2026-10-03, WP-6) — per-candidate RNG: common random numbers by default

**Decision.** `evaluate_many` scores every candidate with a copy of the run `Env` whose `rng` is
reset to `env.rng_for("evaluate")` — the *same* stream for every candidate (common random numbers).
`crn=False` switches to `env.rng_for("evaluate", ParamSet.digest())`.
**Reason.** Results must not depend on evaluation order or worker count (architecture
"Seeds"), and comparisons between candidates are far less noisy when they face the same simulated
paths — which matters for the 20 % materiality rule and for successive halving at low fidelity.
**Consequence.** `workers=1` and `workers=N` give bit-identical tables (tested). Studies should
still draw scenario paths from `env.rng_for(scenario, …)`; a study that wants independent noise per
candidate passes `crn=False` through `run_group`.

## D-WP6-2 (2026-10-03, WP-6) — search axes are anchored on the current value

**Decision.** An axis is `base + k·step` inside the registry `bounds` (anchored on the lower
bound only when the base lies outside them, i.e. a regtest-scale set). Grids are thinned to
`budget.grid_points` per axis keeping the base value; full grids above `cap` (4,096) are thinned
further, and if even two points per axis do not fit, LHS with `cap` samples is used. Both
fallbacks warn.
**Reason.** The current value must be a candidate (materiality), and "±1 step" has to mean the
same thing in a grid, a neighbourhood check and the joint Sobol pass.
**Consequence.** Some grid points sit closer to a bound than one step without touching it; the
bounds themselves are only included when they are on the base lattice.

## D-WP6-3 (2026-10-03, WP-6) — successive halving over Monte-Carlo paths ("Hyperband-lite")

**Decision.** Fidelity is `Budget.paths`. Rung `r` of `R = budget.halving_rounds` runs at
`max(min_paths, ceil(paths / eta^(R−1−r)))` paths (`eta = 3`, `min_paths = 8`), the last at the full
budget; each rung keeps the best `ceil(n/eta)` (feasible first) **plus the current set**. A single
bracket is run (no Hyperband bracket sweep).
**Reason.** The candidate lists are small and fixed by each study, so the Hyperband hedge over
starting fidelities buys little; keeping the current set guarantees the materiality comparison is
made at full fidelity.
**Consequence.** `optimize_group(method="halving")` returns a table of full-fidelity survivors only;
the low-fidelity scores are kept in `GroupRun.halving.low_fidelity` for the report.

## D-WP6-4 (2026-10-03, WP-6) — rejected candidates are counted, base is always evaluated

**Decision.** Candidates failing an invariant, a feasibility predicate, a coupled-bounds check, or
construction are recorded as `Rejected(changes, reason, detail)` and summarised by invariant name
(`CandidateSet.invalid_counts()`); the summary goes into every Recommendation's "Search:" note. The
base set is always evaluated, even if it violates an invariant (a warning is recorded).
**Reason.** "Why was this value not considered?" must have an answer in the report; and
`decide_with_materiality` needs the current row.

## D-WP6-5 (2026-10-03, WP-6) — robust selection and tie-breaking

**Decision.** `robust_select` builds the feasible set from `Constraint`s (policy bounds on an
aggregate — worst / CVaR / mean / quantile — of a metric over scenarios) and the per-scenario
`Metrics.constraints`; minimax regret is measured against the best *feasible* candidate per
scenario. If nothing is feasible it returns the least-violating candidate (sum of relative
violations) with `blocked=True`. Ties within `1e-12·max(1,|best|)` go to the current set, then to
the smallest step distance, then to input order. CVaR uses the fractional-atom (Rockafellar–Uryasev)
definition; for a loss the tail is the upper `1 − alpha` mass.

## D-WP6-6 (2026-10-03, WP-6) — sensitivity estimators

**Decision.** Sobol: Saltelli design on a scrambled Sobol' sequence (`scipy.stats.qmc`; `n` rounded
up to a power of two), Jansen estimators for both `S1` and `ST`, 95 % percentile bootstrap CIs; rows
with a NaN output (an invariant-violating set) are dropped and counted. Morris: random trajectories
with a jump of `⌊n_levels/2⌋` levels (Δ = p/(2(p−1)) for even p), effects per unit of the factor's
range. OAT slope classes use arc elasticity with a 5 %-of-max floor on the denominator: flat
`|e| < 0.1`, steep `|e| ≥ 1`.
**Reason.** Validated against the Ishigami analytic indices (S1 ≈ 0.314, 0.442, 0; ST ≈ 0.558,
0.442, 0.244 — within 0.02 at n = 8,192) and an additive linear model (S1 = ST, exact Morris μ*).
**Consequence.** Morris with 4 levels ranks Ishigami's x1 and x2 together ahead of x3 (x3's effect
is pure interaction: σ > μ*); that is the known behaviour of the method, so Sobol `ST` is the
quantity the joint pass uses for "insensitive" (`policy.insensitive_total_order`).

## D-WP6-7 (2026-10-03, WP-6) — `ybcal sensitivity` CLI deferred

**Decision.** `ybcal.optimize.cli.cli_sensitivity` is not implemented by WP-6: it needs the joint
recommended set and the study evaluators (WP-7/WP-8). The command keeps exiting 2 ("not implemented
yet (WP-6)"). The library pieces (`ParamSetObjective`, `factors_for_params`, `morris`, `sobol`) are
ready for whoever wires it (suggested: WP-8 alongside `recommend`).

## D-WP1-1 (2026-10-03, WP-1) — how the reference model is vendored

**Decision.** `yellowback_model.py` and `yellowback_attest.py` are vendored whole (as
`model/reference.py`, `model/reference_attest.py`) with exact, counted import-line rewrites only;
from `yellowback_util.py` / `util.py` only the needed top-level definitions are copied verbatim by
AST (`model/reference_util.py`). Each `.py` carries a pin header; `VENDOR.json` lists hashes;
`vendor.VENDORED_COMMIT` holds the full pin.
**Reason.** The golden replay needs the attestation helpers (bundle parsing, signature check, W9
selection); `yellowback_util.py` imports the node test framework and cannot be imported standalone.
**Consequence.** `ybcal verify` fails on any edit of a vendored file; re-pinning is
`ybcal verify --revendor --ycash6 PATH --ref NEW` plus bumping `VENDORED_COMMIT` (and the WP-0
snapshot, D-log entry). Node-driver functions of `reference_attest` are intentionally unusable.

## D-WP1-2 (2026-10-03, WP-1) — kernels follow the C++ where the reference model differs

**Decision.** On degenerate inputs where `yellowback_model.py` and `math.h` disagree (weighted
quantile with zero total or q > 10^4; σ with non-positive samples; non-positive amounts), the
kernels implement `math.h`, and the property tests compare with the reference on its domain only.
**Reason.** The node is the consensus; the differences are listed in architecture.md ("Model and
kernels") and never arise on the golden chain or in the worked examples.

## D-WP1-3 (2026-10-03, WP-1) — undefined encodings

**Decision.** Scalar kernels use `None` for undefined (predicates `False`); vectorised kernels use
int64 with `-1` (`vkernels.UNDEF`) and read any price ≤ 0 as undefined, exactly as the C++ does.
**Consequence.** Simulator code (WP-3..5) stores prices as int64 arrays with `-1` gaps.

## D-WP1-4 (2026-10-03, WP-1) — rolling medians by wavelet matrix

**Decision.** PRICE-1 rolling lower medians with min-fill are computed by a wavelet matrix over the
compressed quote sequence (exact range k-th smallest, vectorised over all blocks), not the PLAN
§3.3 sliding sorted window.
**Reason.** O(n log n) independent of W, no per-block Python loop: 1 path × 100k × W=2016 in
≈ 0.07 s (target 2 s); one structure serves all three windows. PIN-1's per-height key exclusion is
not expressible as a static mask, so pinned heights must use the scalar kernel.

## D-WP1-5 (2026-10-03, WP-1) — reference model's stale mainnet `abandon_blocks`

**Decision.** Recorded, not patched: `yellowback_model.Params.mainnet()` has `abandon_blocks =
4,032` while `params.cpp` @ 7702d22 has 34,560 (W21). The kernels never read the model's Params
(parameters are arguments; the registry is the source), and `ybcal verify` prints the drift.
**Consequence.** Worth reporting upstream (ycash6 test framework); a re-vendor after a fix
removes the note, and `test_reference_mainnet_column_drift_is_only_the_known_w21_one` will then
need updating.

## D-WP2-1 (2026-10-03, WP-2) — `PricePath` stays in `ybcal.types`; helpers in `ybcal.data.pricepath`

**Decision.** The frozen `PricePath` (WP-0, `types.py`) is used unchanged; `ybcal.data.pricepath`
re-exports it and adds units/clamp, block ↔ hour resampling (hour → block holds each price for
its 48 blocks; block → hour samples blocks 0, 48, …, so a round trip is exact), slicing, log
returns and CSV/NPZ persistence. Per-step masks travel in `meta` (notably `meta["filled"]`).
**Consequence.** No contract change was needed.

## D-WP2-2 (2026-10-03, WP-2) — loaders: last duplicate wins, as-of resampling with a filled mask

**Decision.** Every loader sorts by time and keeps the **last** row of a repeated timestamp
(`pinrate.py`'s rule; `spreads.py read_log` keeps both, ybcal dedupes so a log is a function of
time), counting dropped and conflicting duplicates. Grid resampling is an as-of join (last
observation at or before the grid point) and flags points with no observation in their cell;
fitting and `describe` use observed points only.
**Reason.** Forward-filled hourly prices on a block grid would otherwise read as 47 zero returns
and one large one, biasing every volatility and tail estimate.

## D-WP2-3 (2026-10-03, WP-2) — fetch: chunked hourly history, explicit network-blocked error

**Decision.** `ybcal data fetch --source coingecko --granularity hourly` fetches more than 90 days
of hourly data through consecutive `market_chart/range` calls of ≤ 89 days; `auto` keeps
CoinGecko's own granularity (hourly ≤ 90 days, daily beyond). Unreachable hosts raise
`NetworkBlockedError` (exit 3) pointing to `docs/data.md`; every fetch writes a provenance sidecar
with the CSV's sha256. `tickers` and `nonkyc` are snapshots appended per call (cron-driven logs).
**Reason.** The owner needs ≥ 1 year of hourly YEC/USD (PLAN §12.3) and a plain `days=365` call
returns daily points. The sandbox cannot reach the APIs, so fetchers are tested on recorded
fixtures with `urlopen` mocked.

## D-WP2-4 (2026-10-03, WP-2) — synthetic presets are placeholders; fitting methods

**Decision.** Presets (all ≈ 115–120 % annualised vol, $0.40 start): GBM σ 1.20; Merton σ 0.95,
λ 12/yr, jumps N(−2 %, 20 %); GARCH(1,1)-t hourly α 0.06, β 0.93, ν 4; regime switch calm σ 0.80 /
turbulent σ 2.00 (mean spells 120 d / 30 d, turbulent drift −150 %/yr); bootstrap has no preset.
Fitting: GBM moments; Merton threshold moments (4 robust sds) polished by a one-jump-per-step
mixture MLE; GARCH-t MLE (L-BFGS-B, three starts); regime switch Baum–Welch EM; bootstrap stores
returns (mean block one week). Discrete-time models (GARCH, bootstrap) refine to finer grids with
a variance-matched Brownian bridge.
**Reason.** PLAN §4.2 asks for moment matching for non-GARCH models; the Merton threshold
estimator alone undercounts small jumps (≈ 25 % low on a test with λ = 50/yr), so the MLE polish
is added and documented. Presets are labelled in every path's `meta` and stay `synthetic`.

## D-WP2-5 (2026-10-03, WP-2) — scenario bases are centred; families via `[[variants]]`

**Decision.** A scenario's base process has its expected log drift removed by default
(`[base] center = true`), so the price program alone sets the trend. Parameterised families
(`oracle-attack-{p}`, `attestor-outage-{n}`, `attestor-capture-{w}`, `hashrate-drop-{to}`,
`dev-absence-{days}`) are one file each with `[[variants]]` and dotted-path `set` overrides.
Behaviour schedules use a fixed, documented name list (unknown names are rejected); a schedule is
`(n,)` when deterministic and `(n_paths, n)` when it has stochastic outages. The `core` tag defines
`Budget.scenario_set = "core"` (11 scenarios).
**Reason.** An uncentred 120 %-vol GBM adds −72 %/yr of log drift, which would turn every
scenario into a bleed. Shipped variants: oracle-attack 10/20/25/34/40/51, attestor-outage 1/2/3/5,
attestor-capture 10/20/25/33/40/50, hashrate-drop 70/60/50/45/30, dev-absence 14/30/60/90/180.

## D-WP2-6 (2026-10-03, WP-2) — `--kind hashrate` is the pool-share CSV

**Decision.** WP-0's `ybcal data import --kind hashrate` reads the PLAN §4.1 pool-share CSV
(`height,payout_key`); shares, rolling shares and a `HashrateDrift` fit come from it. Depth has
two accepted forms: summary `ts,depth_2pct_usd,volume_24h_usd[,bid_depth_2pct_usd]` and book
levels `ts,side,price_usd,size_yec` (summarised to USD depth within ±2 % of the mid).

## D-WP9-1 (2026-10-03, WP-9) — time-scaling rules

**Decision.** `scale_to_regtest` divides block counts by one cadence factor (default
`pSlowWindow / 64` = 31.5) and, optionally, a separate `term_factor` for grace, classes,
abandonment and bond lifetimes (default: the same). Rounding is half-up for block counts. Thresholds
are `⌈c·S′/S⌉` of `signalWindow` with the §1.4 ordering re-imposed. The σ sample count
`volWindow/volStep` is kept exactly: `volStep` is whichever of ⌊volStep/f⌋ or ⌈volStep/f⌉ gives a
window nearer `volWindow/f`, giving 2 / 84 at the default. Counts over a scaled window
(`pinMinTags`, `pinMinBundles`, `dormancyMinBundles`) keep their rate, floored at 2 (two equal
observations). Two more floors: `attestInterval` ≥ 4 (agent cadence vs ~2 s blocks) and
`peerLag` ≥ ⌈peerMin/2⌉. Selection counts, `peerMin`, `valveBlocks`, bps, amounts and constants are
not scaled.
**Reason.** PLAN §6.3: keep the ratios the rules read, and report what integers cannot hold. One
factor keeps term-to-window ratios. The shipped regtest column instead compresses terms ~1,440×, so
`--term-factor` exposes that choice rather than hiding it.
**Consequence.** At the default factor, 37 values differ from the shipped regtest column. Every
difference is explained in `SHIPPED_REGTEST_NOTES`, and the test suite fails on an unexplained one.
Ratio losses over 5 % are listed in `docs/devnet.md` §3.

## D-WP9-2 (2026-10-03, WP-9) — `bondMin` on a scaled set is the regtest 10 YEC

**Decision.** By default the scaler sets `bondMin = 10 · COIN` (the shipped regtest value) and leaves
every other amount unscaled; `bond_min="mainnet"` keeps 20,000 YEC.
**Reason.** A devnet wallet cannot fund three 20,000-YEC bonds in a reasonable number of blocks, and
bond size only enters bond weight, which the devnet does not calibrate.
**Consequence.** Weight-capture studies (G8) must not read bond economics off devnet runs.

## D-WP9-3 (2026-10-03, WP-9) — contract note: the `sunset` invariant at regtest scale

**Request to WP-0.** `invariants._sunset` accepts `enforceUntilHeight = 0` at regtest scale, but a
non-zero regtest sunset must still equal `startHeight + BLOCKS_PER_YEAR`, which is a mainnet-scale
clause. A scaled sunset (`start + 420,480 / term_factor`) or a flag such as
`-yellowbackenforceuntil=500` is legitimate on regtest.
**Interim.** `scaling.check_regtest(ps)` drops only that clause for regtest-scale sets with a
non-zero sunset. The scaler and the overlay split use it. The scaler defaults to
`enforceUntilHeight = 0` (the registry's regtest default), so the default path never needs it.
**Proposed fix.** Scope the `u == s + BLOCKS_PER_YEAR` clause to mainnet scale (keep
`u > startHeight` at regtest scale).

## D-WP9-4 (2026-10-03, WP-9) — own minimal launcher beside `yellowback-devnet`

**Decision.** `ybcal devnet run` starts its nodes itself by default (`runner.MinimalDevnet`), using
the single-node configuration of `doc/yellowback-devnet.md` §2 plus the devnet's fixed pool keys
and port-seed scheme. `--launcher` drives `contrib/yellowback/devnet/yellowback-devnet up` only when
the overlay's six runtime flags equal what that launcher hard-codes.
**Reason.** At the pin the launcher builds every node's arguments with `yellowback_node_args`
(`-yellowbackstartheight=1 -yellowbacksigmaref=0`) and has no option to pass other node arguments,
so the six runtime parameters could not be varied through it (PLAN §6.2 item 3). The minimal
launcher also has a deterministic bootstrap (101 funding blocks plus a full activation) that the
simulator can replay exactly.
**Consequence.** `attestor-outage-1` needs attestor seats with real `yellowback-attest` agents, so it
runs only under `--launcher`, and is skipped otherwise. Personas (`yellowback-sim`) need the
launcher's role presets and are not driven in v1; `replay(on_step=…)` is the hook for them.

## D-WP9-5 (2026-10-03, WP-9) — differential contract and pass criterion

**Decision.** The simulator entry point is
`ybcal.sim.engine.simulate_devnet(params, path, schedule) -> list[dict]` (records as
`scrape.HISTORY_FIELDS`). `diff.compare` requires exact equality with type discipline: `None ≠ 0`
and `bool ≠ int`. An allowlist entry is `field`, `field@h` or `field@lo-hi`. Keys present on one
side only fail the comparison. `pending` (no simulator) and `skipped` (no node) never count as a
pass.
**Consequence.** WP-3..5 implement `simulate_devnet` against `devnet.scenarios.ReplayStep`
semantics. Until then `ybcal devnet validate` reports every scenario `pending WP-3..5` and exits 0
(3 with `--strict`).

## D-WP9-6 (2026-10-03, WP-9) — version-skew policy

**Decision.** A binary whose commit is not the pin, or cannot be determined, is refused unless
`--allow-version-skew` is passed. This covers the relations predates, postdates, diverged and
unknown. The refusal lists the commits in between and the parameter values that differ on both
networks. A running node's `yed_getinfo.params` and `yed_getactivation` must equal the overlay,
except the node-overridable wallet policy (`nPenalty`, `accuracyWindow`, `payeeTiltBps`).
**Finding.** CI run 37081639884 is built from `94bafa4`, eight commits before `7702d22`. Its regtest
column is identical; mainnet `abandonBlocks` differs (4,032 vs 34,560). The rule changes are the
W20 soft supply cap (`deff6f5`) and the `mintingAllowed` / `supplyCapReached` RPC predicate
(`a8291a0`). With `-yellowbacksupplycapbps=0` W20 has no effect, so that binary is usable for
stock-column runs under `--allow-version-skew`. It is not usable for runs with a cap, such as a
scaled mainnet overlay's 1,500 bps.

## D-WP9-3 resolution (2026-10-03, integrator)

`invariants._sunset` now applies the `startHeight + BLOCKS_PER_YEAR` clause at mainnet scale only;
at regtest scale a non-zero sunset must merely lie after `startHeight`. `scaling.check_regtest`'s
filter is now redundant but harmless and is kept.

## D-WP3-1 (2026-10-03, WP-3) — engine stages: two hook-only additions

**Decision.** The stage list agreed with WP-5 (`activation, attest, pin, price, sigma, supply,
halts, vaults`) is kept and extended with `judge` (first; REG-4 runs first in SNAP) and `dormancy`
(last). Neither has a built-in step. Hooks run after each stage's built-in step and may mutate the
series. After the `vaults` hooks the engine recomputes `global_ratio_bps` and the HALT-2 bit from
`supply_cents` / `collateral_zat`, so WP-4 only has to fill those arrays.
**Consequence.** Additive; hooks that ignore unknown stages are unaffected.

## D-WP3-2 (2026-10-03, WP-3) — internal activation is exact, not "always ACTIVE"

**Decision.** When WP-5's `activation.simulate` is absent the engine uses the exact ACT-1..3 and
ACT-4/6 kernels (`vkernels.signal_counts` / `hysteresis` series), tested equal to the reference
model; the "always ACTIVE, no ACT halts" behaviour is an explicit flag (`activation_mode=
"always_active"`) for price-only studies. `series.activation_source` records which ran.

## D-WP3-3 (2026-10-03, WP-3) — PIN-1 lives in the engine; pinned keys are pool ids

**Decision.** The `pin` stage computes PIN-1 itself from BundleLog rows (`pin1_triggered`, or
`bundle_present` + `bundle_a_mint` on WP-5's AttestSeries / in `inputs.attest`) and the quote tags;
pinned keys are pool ids stored as a uint64 bitmask (≤ 64 pools). PIN-2 (`pinned_seqs`) stays WP-5's.
Heights with pinned keys take the exact scalar median path (D-WP1-4); others the wavelet fast path.

## D-WP3-4 (2026-10-03, WP-3) — Ycash subsidy schedule and issuedZat origin

**Decision.** `issuedZat` = Σ `GetBlockSubsidy(h)` over `[startHeight, H]` (state.cpp:1224; the
virtual snapshot carries 0). Mainnet schedule from ycash6 @ 7702d22: slow start 20,000, halving
840,000 pre-Blossom / 1,680,000 post, Blossom 1,100,000, `UPGRADE_YCASH` 570,000 changes nothing in
the subsidy (the YDF is paid out of it), no funding streams. Regtest uses Blossom at 1 (the
functional tests' `nuparams`, `reference.regtest_subsidy`).
**Consequence.** At `startHeight` 3,075,000: 1.5625 YEC/block until 3,960,000 → 657,000 YEC per
sunset year. Feeds fact 1.5-2 / G7.

## D-WP3-5 (2026-10-03, WP-3) — hour-mode kernel tolerance (request to WP-0/WP-8)

**Decision.** `engine.KERNEL_TOLERANCE_P95_BPS = 300` (p95 relative error of hourly pMint/pClaim vs
block mode) is the default acceptance; measured ≈ 50 bps on held-out GBM paths.
**Request.** Add a policy key (e.g. `kernel_tolerance_p95_bps`) so the owner sets it; WP-3 did not
edit `config.py` / `policy/default.toml` (WP-0 files).

## D-WP3-6 (2026-10-03, WP-3) — oracle model choices

**Decision.** A pool's quote is the integer TWAP of the true price over `twap_blocks` (default 12 ≈
15 min) × (1 + (bias + noise·z)/10^4), clamped to [PRICE_MIN, PRICE_MAX]; stale feeds repeat a
quote for `refresh_blocks`; outages are Poisson starts with exponential lengths. Floats are used
only to generate behaviour; the stream handed to the rules is integers. `BlockInputs` applies TAG-2:
a quote outside the price range makes the whole tag absent (as `find_tag` does).
**Finding for G2 (WP-7a).** The σ estimate SIGMA-1 sees is measured on pFast (a 96-block median of
TWAP quotes), which smooths returns: on GBM at 120 % true volatility the median σ̂ is ≈ 8,200 bps
against ≈ 11,900 bps measured on the true price. `sigmaRefBps` should be calibrated against σ̂ of
simulated/real *pFast*, not raw price volatility (`sigma.sigma_hat_bps` on `series.p_fast`).

## D-WP3-7 (2026-10-03, WP-3) — `simulate_devnet` and a WP-9 test adjustment

**Decision.** Implemented WP-9's contract (D-WP9-5) as `engine.simulate_devnet`, reusing WP-9's
`block_miners` / `jittered_quote` so the replayed tag stream is identical (pools signal-only while a
step's price is 0, dark miner untagged, block i = height 1 + i). Its runner defaults (3 pools,
10 bps jitter, run seed = schedule seed) are keyword arguments and must match the devnet run.
**Consequence.** `ybcal devnet validate` now finds a simulator, so scenarios report SKIPPED (no
ycashd) instead of PENDING; `tests/devnet/test_diff_cli.py::test_cli_validate_pending` was relaxed
by one line to accept either (WP-9's file — flagged for the integrator).

## D-WP3-5 resolution (2026-10-03, integrator)

Added `Policy.hour_kernel_tolerance_bps` (default 300) and the matching `policy/default.toml` key;
`engine.KERNEL_TOLERANCE_P95_BPS` stays as the library default. D-WP3-7 (WP-9 test accepting
SKIPPED as well as PENDING) is accepted as is.

## D-WP5-1 (2026-10-03, WP-5) — attestation walk: segments of constant status + sparse points

**Decision.** `attest.simulate` walks each path through segments between status events
(registration, maturity, EQV-1, bond spend, REV-1); seating for a whole segment is one numpy
argsort (or constant when every ELIGIBLE seq fits in `nSlots`), and a per-path Python loop visits
only demand, PIN-2-trigger and dormancy-check heights. A dormancy that fires cuts the segment.
**Reason.** Exact node semantics (block order: transactions, BundleLog[H], SNAP) at ≈ 0.1 s per
mainnet path-month; attestation events are sparse.
**Consequence.** Exactness is shown by replaying the golden chain and by a reference harness that
drives `YellowbackModel`'s own SNAP/selection code with synthetic transactions (8 randomised
scenarios); seated/selected/pinned sets are uint64 bitmasks, so ≤ 64 registered attestors per path.

## D-WP5-2 (2026-10-03, WP-5) — attestor behaviour and bundle assembly model

**Decision.** An online attestor signs every `k` blocks at its phase (`attestInterval`); a bundle for
`(R, selector)` contains every selected seq whose newest attestation is cited in
`(R − attestMaxAge, R]` (BuildBundle, index.cpp:1174) and verifies iff `mSelect ≤ |C| ≤ bundleMax`.
Attestations cited at or after a seq's ejection / bond spend are not usable (the pool refuses
EJECTED/WITHDRAWN, index.cpp:1132). Availability: iid or two-state Markov per attestor plus an
optional common outage; prices `true·(1 + bias + noise)`, optional stuck feed. REV-1 is sent at the
first height ≥ dormancy + `revive_delay` with an own attestation cited in `(H − attestMaxAge, H − 1]`.
Default demand: Poisson(1/48) bundles per block with `ref = H − DEFAULT_REF_LAG` (WP-4 replaces it).
Bond spends are clamped to `registerHeight + bondMinLock + 1` (the earliest CLTV spend); an EQV-1 at
the spend height is ordered first.
**Reason.** Agent policy is not consensus; these are the simplest models consistent with the node's
wallet path and the G8 questions (liveness, dormancy, capture).

## D-WP5-3 (2026-10-03, WP-5) — PIN coupling with the oracle engine

**Decision.** `attest.simulate` reads the engine's xMint (`series.p_mint`) for PIN-2 and emits the
PIN-1 trigger (`pin1_triggered`, from the BundleLog rows) for the engine to apply the key exclusion.
The exact coupled result is the fixed point of oracle → attest → re-median at PIN-1 heights → attest.
**Reason.** PIN-1 at H reads rows < H and PIN-2 at H reads pMint < H, so only the rare pinned heights
couple the two layers; a block-interleaved engine would be much slower for no gain elsewhere.
**Consequence.** Recorded as a contract note for WP-3 (architecture.md, "Activation & attestation
simulator").

## D-WP5-4 (2026-10-03, WP-5) — mid-chain starts and height frames

**Decision.** Activation needs `ActivationInit` (incl. the last `W − 1` signal bits) when
`height0 > start_height`; attestation applies pre-series events at `height0` and accepts
`initial_trigger_height` / `initial_seated_since`. The sunset is re-based:
`enforce_until = start_height + (P.enforceUntilHeight − P.startHeight)` (0 stays "none").
**Reason.** The engine may run relative heights or a window of a longer chain; the rules depend only
on distances from the start.

## D-WP5-5 (2026-10-03, WP-5) — G5 analytic estimators

**Decision.** For iid Bernoulli(p) signals the exact per-block downcrossing probability
`P(Bin(W − 1, p) = floor − 1)·p·(1 − p)` is the expected rate of entering `count < floor`; it bounds
the halt-episode rate from above (an episode starts only at a downcrossing while not halted). The
halted fraction is bracketed by `P(count < floor)` and `P(count < resume)`; `P(any per year)` uses
`1 − exp(−rate)`. Share drift uses the exact Poisson-binomial; detection delay is Monte Carlo with a
fluid approximation; the ACT-7 valve uses the gambler's-ruin `(q/(1 − q))^(valveBlocks − 1)`.
**Reason.** Overlapping windows make per-window binomials double-count; crossings are exact and
cheap. All estimators are checked against simulation in `tests/sim/test_activation_analytic.py`.

## D-WP5-6 (2026-10-03, WP-5) — G8 analytic estimators

**Decision.** Liveness: binomial in the per-attestor freshness `1 − (1 − u)^⌈maxAge/k⌉` (Markov:
`1 − (1 − u)(1 − 1/L)^((s − 1)k)`), beta-binomial with intra-class correlation ρ for correlated
outages. False dormancy: closed form for iid availability, an exact forward recursion over the
window for Markov outages; per-year figures are a union bound over checks. Capture: bundle-level
thresholds (qLow / 1 − qLow) plus Monte Carlo over W9 selections with the exact kernels.
**Reason.** Matches PLAN §5.8; each helper is validated against Monte Carlo and the liveness formula
against the simulator's own bundle success rate.

## Integration of WP-3 and WP-5 (2026-10-03, integrator)

- The engine reads `activation.simulate`'s `ActivationSeries` fields (it had assumed a tuple) and keeps
  the series on `BlockSeries.activation_series`.
- `attest.simulate` runs only when `inputs.attest` is given; without it the run is unarmed.
- D-WP5-3 item 1: `oracle.generate_block_inputs(..., enforce_until=)` applies the sunset signal mask
  (miners drop the bit past `enforceUntilHeight`). The engine itself does not mask, so replayed tag
  streams stay exact.
- D-WP5-3 item 2 (iterating PIN-1/PIN-2 coupling to a fixed point) is not yet done: the engine runs
  one pass (oracle → attest → PIN-1 → medians). Assigned to WP-8.

## D-WP7a-1 (2026-10-03, WP-7a) — G1 objective is normalised by the current windows

**Decision.** PLAN §5.1's "crash-lag CVaR₉₅ plus λ × pump overpricing" adds hours to bps, so λ would
mean nothing. The study uses J = CVaR₉₅(pClaim 90 % crash lag)/current + λ · E[pMint − true | pump-dump]/current,
both normalised by the current windows' values; λ = `pump_overpricing_lambda` is then a relative
weight (0.5: half a crash lag's worth of relative change). The crash-lag term is the **pClaim** 90 %
lag (pClaim = max(pMid, pSlow) is the price that lags a fall); pMint lags are reported.
**Consequence.** J(current) = 1 + λ; materiality applies to J.

## D-WP7a-2 (2026-10-03, WP-7a) — G1 manipulation constraint: analytic V16 plus a simulated check

**Decision.** `attack_share_min` holds when (a) every window's V16 threshold (smallest coalition
share whose quotes are the lower median with probability ≥ ½, exact binomial, coalition carved out of
the tagging share) is ≥ `attack_share_min`, and (b) a coalition of exactly that share, biasing ±10 %,
moves pMint up or pClaim down by ≥ half its bias in ≤ 5 % of attack blocks (`ATTACK_MOVED_TOL`).
**Reason.** (a) is nearly window-independent (≈ 40 % of hash at 80 % tagging), so on its own it never
discriminates; (b) catches the rank-shift effect (a minority pushes the lower median up the honest
quotes, whose spread grows with the window). Only the harmful directions count (over-minting,
premature claims); griefing moves are reported.
**Request to WP-0.** Add a policy key `attack_moved_tol` (default 0.05) so the owner sets (b).

## D-WP7a-3 (2026-10-03, WP-7a) — G1/G2 pool-outage model and NO_PRICE availability

**Decision.** Background availability (`no_price_h_per_year`, the `max_no_price_hours` constraint)
is measured in calm with independent per-pool feed outages: each of the policy's equal pools has
1 outage per 30 days, exponential length with mean 4 h, signal-only tags while out. A 6-hour outage of
every feed is reported per event (`no_price_h_per_feed_outage`), not annualised.
**Reason.** The policy has no outage frequency; annualising the all-feeds event at any assumed rate
≥ 1/yr makes every window set infeasible (≥ 6 h per event), which says nothing about windows.
**Finding.** At 80 % tagging with 6 equal pools, one pool out leaves a 66.7 % tag rate — exactly the
⌈2W/3⌉ fill — so pMid at 576 blocks flickers to NO_PRICE during long single-pool outages
(≈ 16–20 h/yr under this model; 1,152 blocks ≈ 0.4–4 h/yr). The constraint, and with it the G1
recommendation, rests on this placeholder; measured pool tagging data should replace it.
**Request to WP-0.** Policy keys `pool_outage_rate_per_day` / `pool_outage_mean_hours` (and
optionally `feed_outages_per_year`).

## D-WP7a-4 (2026-10-03, WP-7a) — G1 keeps HALT-3 working: recall constraint on crash-70-1d

**Decision.** A window set must keep HALT-3 firing within one day of the crash start on ≥
`halt_recall_floor` of `crash-70-1d` paths at the current `divergenceBps`.
**Reason.** HALT-3 compares the medians, so G1 can silently disable it (G7 coupling). The slow
30-day `crash-90-30d` never trips HALT-3 at 20 % divergence for any window set; its recall is
reported, not constrained (that is G7's question).

## D-WP7a-5 (2026-10-03, WP-7a) — G2 window rule also bounds the K12 trap; responsiveness on σ̂

**Decision.** volWindow/volStep must keep both the regime-shift responsiveness and the K12 cap trap
after a 6-hour feed outage (blocks with an undefined sample after the feeds return, ≈ volWindow +
pFast recovery) within `max_sigma_lag_blocks`. Responsiveness is measured on the unclamped σ̂ (median
over paths), not the clamped multiplier, which is flat at 1× whenever both regimes sit below the
reference.
**Reason.** The trap is the other way the multiplier fails to reflect the market (fact 1.5-3), with
the same lag tolerance; without it the CV rule always prefers the longest admissible window.

## D-WP7a-6 (2026-10-03, WP-7a) — sigmaRefBps: round down; KEEP inside the M14 band

**Decision.** The rule value is the realised median pFast-based σ̂ rounded **down** to 500 bps (so
the unclamped median multiplier is ≥ 1×, inside the M14 band); the band test uses the unclamped
ratio σ̂₅₀/sigmaRef (the clamped median is ≥ 1× by construction). The current value is kept while that
ratio lies in `sigma_accept_band` and the rule value is within `materiality`; the cap is kept while it
covers the p99 turbulent multiplier and is within `materiality` above the rule value.
**Reason.** PLAN §2.3 (minimal change) applied to rules that compute a value directly rather than
search for one.

## D-WP7a-7 (2026-10-03, WP-7a) — evidence directory and shared realisations

**Decision.** Studies write evidence to `env.data["out_dir"]/<group>/` (or `env.data["workdir"]`), else
a fresh temp dir. `decide()` has no `Env`, so `evaluate` puts `out_dir` (and the budget name) into
`Metrics.meta`. G1 and G2 share one per-process memo of scenario realisations and medians
(`g1_price_windows.realise` / `median`), keyed by seed, scenario, paths, horizon, data hash and the
policy's pool parameters.
**Request to WP-0/WP-8.** An `Env.out_dir` field would make this explicit.

## Contract changes from WP-7a (2026-10-03, integrator)

- `Policy` gains `attack_moved_tol` (0.05), `pool_outage_rate_per_day` (1/30) and
  `pool_outage_mean_hours` (4.0); G1/G2 read them (module constants remain as fallbacks).
  `feed_outages_per_year` was not added: the all-feeds outage stays reported per event (D-WP7a-3).
- `Env.out_dir` added; `g1_price_windows.out_dir_of` prefers it over `env.data["out_dir"]`.

## D-WP7c-1 (2026-10-03, WP-7c) — G5/G8 as families of one-at-a-time rules

**Decision.** G5 and G8 each run one candidate table of one-at-a-time sweeps around the current set
and apply one decision rule per *family* of parameters (`g5_activation.Family` / `FamilyStudy`, shared
by G8): the family's rows are those whose delta lies in its params, its metrics are re-projected onto
its primary and its own constraints, then `decide_with_materiality` (optimize), "KEEP unless a
constraint fails, then the nearest feasible" (verify, primary ≡ 0), or a ported closed-form target with
a materiality tie to current (rule). `adjust_changes` reconciles families that touch the same thing
(G5: a new signalWindow carries its thresholds at their fractions; G8: when both dormancy families fix
one violation only the faster fix is applied, the other is KEEP with a note).
**Reason.** The groups hold many loosely coupled parameters (26 in G8); a joint grid is wasteful and
mixes unrelated constraints into one feasibility test. The WP-8 joint pass handles cross-group coupling.
**Consequence.** Simulation confirmation (exact `activation.simulate` / `attest.simulate`) runs inside
`decide` from `Metrics.meta` (seed, budget name/paths/horizon, out_dir), since `decide` has no `Env`.

## D-WP7c-2 (2026-10-03, WP-7c) — judgement constants; requested policy keys

**Decision.** Tolerances the policy lacks are module constants (`g5_activation.JUDGEMENT`,
`g8_attestation.JUDGEMENT`), documented in docs/studies/g5.md and g8.md. Requested policy keys
(integrator: add to `Policy`/`default.toml`; the studies would read them with `getattr(policy, key,
JUDGEMENT[...])`): `activation_reliability` (0.99), `valve_minority_trip_max` (0.01),
`attestor_mean_outage_blocks` (48), `max_harmful_capture_prob` (0.01), `max_grief_capture_prob` (0.05),
`max_premature_claim_prob` (0.01), `claim_reaction_blocks` (576), `registration_notice_blocks` (8,064),
`min_capture_days` (90), `max_newcomer_seat_days` (365).

## D-WP7c-3 (2026-10-03, WP-7c) — G5 objective: false halts, detection as a constraint

**Decision.** Every G5 threshold family minimises expected false-halt hours/year (0.01-h resolution, a
drift mixture of window-mean shares) subject to the policy's false-halt, flap and detection budgets, an
enforcement majority (`enforcementFloor ≥ W/2`, L3) and reliable activation. Detection delay is a hard
constraint, not the objective. The halted fraction is rate × fluid duration capped by `P(count <
resume)` (conservative near the resume level).
**Reason.** The valve bounds the cost of minority enforcement ("the node rejoins within six blocks"),
whereas a false halt stops minting for everyone; with detection as the objective the rule would shrink
signalWindow to its bound on any policy. The 0.01-h resolution keeps 10⁻²⁰-hour differences from
counting as "material improvements".
**Consequence.** G5 provenance is `judgement` (exact math given an assumed share) unless a pool-share log
is in `env.data["pool_shares"]`/`["hashrate"]` (`real-data`). `false_abandon_probability(params, share,
abandon_blocks)` is the G4 import (an upper bound, tested against Monte Carlo).

## D-WP7c-4 (2026-10-03, WP-7c) — G8 capture is evaluated at bundle level

**Decision.** The proposal §7.2 rule ("Q_LOW must exceed the largest single-entity weight share among the
selected attestors") is checked with the exact selection and weighted-quantile kernels: an entity with
`max_single_entity_weight_share` of the *seated* weight in one seat, honest seats equal. Theft (aMint up)
must stay ≤ 0.01; griefing (spanning qLow, aMint down) ≤ 0.05 — the latter only in the qLow family.
**Reason.** With 6 of 9 seats selected a 25 % seat holds 34.8 % of a bundle's weight, above qLow 3,333,
in 94 % of bundles. That is griefing (over-collateralisation), not theft, which needs > 2/3.
**Consequence.** qLowBps → 3,500 (qHigh 6,500) at the default policy — or the owner lowers the assumed
entity share to ≤ ~0.22. Reported as a design note in the qLowBps recommendation.

## D-WP7c-5 (2026-10-03, WP-7c) — ported spreads.py/pinrate.py; the "200–300" reading

**Decision.** `_g8_ports.py` keeps upstream names and arithmetic (`analyze` renamed `analyze_spreads` /
`analyze_pinrate`); the README's fallback "pick the smaller of the two whose rate clears 20 %" is read
literally (the smallest of 200/300 with arming rate > 20 %, 200 if neither). `pooled_pinrate` pools the
windows of several synthetic histories. The equivalence test imports the upstream scripts from a temp
copy (`git show` at 7702d22 — ycash6 untouched) and skips without a clone; hand-computed cases always run.

## D-WP7c-6 (2026-10-03, WP-7c) — attest.simulate notes found while confirming G8

- `seatedSince ≤ 0` reads as "not seated" (the node's 0 sentinel), so a mid-chain run with a negative
  `initial_seated_since` never makes anyone DORMANT. The G8 confirmation uses positive heights
  (start 1, height0 50,400, a multiple of every dormancyCheck on the grid).
- BundleLog rows before `height0` are unknown, so dormancy in a mid-chain run counts rows from `height0`.
- With `kSlack = 0` a dead selected attestor makes every bundle that picks it fail, so no row lists it and
  dormancy can never eject it (a protocol property, not a simulator artefact): G8 adds the
  `dead_detectable` (k ≥ 1) constraint to the kSlack family and counts a dead attestor's rows only when
  the other `m + k − 1` still make the bundle.

## Contract changes from WP-7c (2026-10-03, integrator)

The ten G5/G8 assumption constants are now `Policy` keys under `[studies_g5_g8]` in
`policy/default.toml`; the studies already read them with `getattr(policy, key, default)`.

## D-WP4-1 (2026-10-03, WP-4) — vault book = vectorised lifecycle plan + exact sequential pass

**Decision.** Each vault's lifecycle (owner redeem, sweeps, RED-4(a)/(b) claims, thefts, NOT-1
notices) is planned vectorised with first-passage searches (sparse tables), because it does not
depend on other vaults; everything order-dependent — refHeight choice, HALT-2 at R from the book's
own totals, MINT-6 against the live supply, VOID — runs sequentially through `mint_verdict` /
`red_verdict` / `notice_verdict`, transcribed from state.cpp and tested verdict-for-verdict against
the vendored model. A planned honest spend the rules refuse is counted (`plan_mismatch`), never
silently applied.
**Reason.** Exactness where the node is exact, and ~0.4 s per 5-year path instead of a per-block
Python loop.

## D-WP4-2 (2026-10-03, WP-4) — wallet preflight, refusals and VOIDs; order inside a step

**Decision.** A minter preflights candidate snapshots against the tip (totals after the previous
step) and sends the first that passes; if none passes the attempt is *refused* (no transaction,
reason = the preferred candidate's verdict). The transaction is judged at confirmation against the
live totals: a mint whose MINT-6 fails only because of a mint earlier in the same block is VOID.
Inside a step, closures are applied before mints, mints in arrival order. Spends confirm only in a
block above `nLockTime` (IsFinalTx): owner path from `lockHeight + 1`, claim path from
`claimHeight + 1`.
**Consequence.** VOIDs in the simulator are cap races (and, in hour mode, nothing else); reorg VOIDs
(`DEFAULT_REF_LAG`, G9) are not modelled.

## D-WP4-3 (2026-10-03, WP-4) — personas

**Decision.** Owner: redeems at the first step with `(collateral − FEE-1 − tx fee) · price ≥ debt ·
YED price` once present (absence: Poisson spells, log-normal lengths; lost keys never return).
Defector persona: sweeps without burning as soon as ACT-5 is off (prefers its sweep to a redeem).
Honest owner: sweeps only under the abandonment predicate and only when it would not redeem first.
Thief: takes the claim path without burning when ACT-5 is off. Claimant: rational, claims at the
first step with the vault underwater at some R of the window (lowest pClaim) **and** profit ≥
`min_profit_bps` of the debt (base slippage; a depth model re-checks); under ARMED it posts NOT-1
when pEmerg is underwater and claims under RED-4(b) at the first persisted, profitable R (lowest
pClaim). Minter: adversarial refHeight = highest admissible pMint (fact 1.5-5) or wallet
`tip − REF_LAG`. Everyone burning YED pays `$1 · (1 + premium_bps)`.
**Request (WP-0 / integrator).** Policy keys for `yed_premium_bps`, `claimant_slippage_bps`,
`defector_share` and `lost_key_prob` would let the owner set them; until then they are
`AgentsConfig` fields with neutral defaults (0, 100 bps, 0, 0).

## D-WP4-4 (2026-10-03, WP-4) — hour-mode conventions and the default kernel

**Decision.** Step `t` = snapshot at the last block of hour `t`; a transaction at step `t` reads
`R = heights[t − 1]` and confirms at `R + DEFAULT_REF_LAG + 1` (the refWindow choice collapses to
one snapshot). Defaults: ACTIVE after the fastest activation, E(R) non-empty, the set renewed at
its sunset (`assume_renewal=True`; W18), unarmed (a bundle proxy is optional). The default kernel
of `simulate_vault_book_hours` is `OracleTransferKernel.ideal(params, substeps=4)`
(`DEFAULT_HOUR_SUBSTEPS`): 44 s instead of 90 s for 200 paths × 5 years on 4 cores, P(bad debt)
within 0.1 pp of WP-3's 12 sub-steps. A calibrated kernel (`calibrate_kernel(..., substeps=4)`)
can be passed instead.

## D-WP4-5 (2026-10-03, WP-4) — bad-debt definitions

**Decision.** Vault-level bad debt is the exact integer test `collateralZat · truePrice <
mintedCents · 10^12` at the first block above `claimHeight` (G3 core, fact 1.5-1) and, for the
grace increment, above `lockHeight`; events beyond the horizon are censored. System-level: unbacked
YED (sweeps, thefts: `Totals.unbackedCents`) plus the uncovered debt of vaults still ACTIVE. The fast
path drops agents, the cap and HALT-2 and equal-weights a 16-point quantile grid of the class's term
distribution.

## D-WP4-6 (2026-10-03, WP-4) — findings for the owner (design notes, not parameter changes)

**Finding.** (1) RED-5's residual is identically 0 under RED-4(a); (2) RED-4(b) pays the claimant
the debt's worth at `max(xClaim, aClaim)`, so it is never profitable to a claimant buying YED at par
— it recovers collateral only as a par exit for YED holders when YED trades at a discount;
(3) pClaim's lag consumes the 10 % claim margin in a steady decline, so profitable claims are rare;
(4) the soft cap counts class-A supply (which bypasses it at σ = 1, W16/W20), so B/C stay closed
whenever A demand alone outruns the cap; (5) FEE-1 on the collateral makes the round-trip fee 2.5 %
of the debt for class A at any size. G3/G6/G7 should report these (docs/architecture.md, WP-4
findings) rather than tune around them.

## D-WP4-7 (2026-10-03, WP-4) — contract notes

No frozen contract changed. Notes for other WPs: `VaultHook(attempts=...)` is indexed by path
*within a chunk* (use unchunked `simulate_blocks` when overriding attempts); the hook uses only
public engine fields (`BlockSeries` arrays, `series.rng`, `extras["vaults"]`); WP-5's
`AttestSeries` feeds the book through `series.armed / a_mint / a_claim` with no further glue.

## D-WP4-3 resolution (2026-10-03, integrator)

Policy keys `yed_premium_bps`, `claimant_slippage_bps`, `defector_share`, `lost_key_prob` added
(`[agents]` in `policy/default.toml`). Studies build `AgentsConfig` from them.

## D-WP7b-1 (2026-10-03, WP-7b) — G3/G4 share one hour ensemble; P(bad debt) is WP-4's fast path

**Decision.** G3 and G4 score every candidate on one per-process hour ensemble
(`g3_collateral.ensemble`): the four synthetic presets (or, with a real price, a block bootstrap of
its hourly returns plus the history itself) through `OracleTransferKernel.ideal` at 4 sub-steps
(D-WP4-4). P(bad debt) is `metrics.p_bad_debt_fast` per member and class, memoised on exactly the
fields it reads, so a ratio sweep costs one fast-path call per new value and G4's grace curve is one
call per member with every grace. Members are aggregated with `optimize.robust.aggregate` under the
policy key `ensemble_agg` (read with `getattr`, default `"worst"` = minimax over the presets).
**Reason.** The ensemble does not depend on G3/G4 parameters; common random numbers make the
materiality comparisons meaningful; `"worst"` is the robust default of `optimize.robust.Constraint`.
**Consequence.** quick G3 ≈ 50 s serial (35 s of it building the ensemble); the strictest preset
(usually `regime`) decides under the default aggregate.

## D-WP7b-2 (2026-10-03, WP-7b) — G3 ratio rule: materiality on the ratio; BLOCKED → least violating

**Decision.** `baseRatioBps[c]` uses `decide_with_materiality` with the ratio itself as the primary
(minimise) and `pbad.c ≤ max_bad_debt_prob[c]` as the constraint: a feasible current value is kept
unless the smallest feasible ratio frees more than `materiality` of the collateral; a violating one
moves to the smallest feasible ratio; with no feasible ratio inside the registry bounds the verdict is
BLOCKED and the recommended value is the least-violating one (lowest aggregate P(bad debt)), with the
trade-off curve in the Recommendation and `g3_tradeoff.csv`. The claim threshold uses the same shape
(primary θ, constraint = claimant margin ≥ `claimant_min_profit_bps`).
**Reason.** PLAN §5.3 "smallest ratio meeting the tolerance" plus PLAN §2.3 minimal change; the
instructions require BLOCKED with the least-violating value rather than a silent cap.

## D-WP7b-3 (2026-10-03, WP-7b) — class boundaries are verified; heterogeneity is a design note

**Decision.** Heterogeneity of a class = (mean P(bad debt) over the longest quarter of its term grid −
mean over the shortest quarter) / class mean, worst member. The internal boundaries are *checked*:
KEEP while MINT-2 contiguity and the CLTV bound hold. Heterogeneity above `class_heterogeneity_max`
raises PLAN §5.3's split/merge suggestion as design note G3-DN6 (the class count is fixed by the
rules), and shifted partitions are scored as evidence only.
**Reason.** Drawdown risk rises with the term in every class (A 2.3, B 1.2, C 0.6 at quick against
0.5): a boundary move only shifts the problem between classes, and PLAN §5.3 says the study
"recommends splitting or merging", which is a rule change, not a parameter value.

## D-WP7b-4 (2026-10-03, WP-7b) — G4 grace: the debt side enters through J only

**Decision.** grace = argmin `J = w_owner·P(miss) + w_debt·ΔP̄` subject to `P(miss) ≤
max_owner_miss_prob`, with `ΔP̄ = 0.4·ΔP_A + 0.4·ΔP_B + 0.2·ΔP_C` (the default demand mix). "ΔP_c >
max_bad_debt_prob[c]" is a reported flag (and design note G4-DN2), not a constraint.
**Reason.** PLAN §5.4 asks for "both under policy", but the policy's bad-debt tolerance is on the
*total* P(bad debt), which G3 meets through the ratios; as a per-grace constraint it is infeasible at
every grace on synthetic data (B's increment exceeds 1 % at 12 days while the owner tolerance needs
≥ 14 days), which would block grace for a reason grace cannot fix.
**Consequence.** abandonBlocks is computed in `decide` at the *recommended* grace (W21), as the closed
form of PLAN §5.4 (smallest whole-day value ≥ max(grace, runbook) with P(false abandon) ≤ policy);
when grace moves, abandonBlocks inherits grace's provenance. Optional policy key
`dev_absence_tolerance_days` (default 0) adds tolerated developer absence to the runbook.

## D-WP7b-5 (2026-10-03, WP-7b) — G9 rules are admissible ranges with a verify rule

**Decision.** Each G9 parameter gets a closed-form admissible range (docs/studies/g9.md); the current
value is kept while inside it, else moved to the nearest admissible lattice value; an empty range is
BLOCKED. `maxMint`'s depth bound is evaluated only with a volume series (`env.data["depth"]`) or the
policy key `yec_daily_volume_p10_usd`; otherwise the needed volume is reported and the value kept
(PROVISIONAL). `walletConfirmations` uses Nakamoto's catch-up probability at `reorg_attacker_share`;
`DEFAULT_REF_LAG` the natural-reorg VOID probability `orphan_rate^(lag+1)` (the attacker's probability
of voiding a mint is reported, not constrained: a VOID releases the collateral). Judgement constants
(`dust_spend_multiple` 3, `residual_max_share` 1 %, `carrier_max_share` 0.1 %,
`mint_inclusion_slack_blocks` 10, `reference_price_usd`) are module constants read through `getattr`.
**Reason.** PLAN §5.9 calls these constraint-driven; moving a value that already satisfies every
constraint would violate PLAN §2.3; inventing a YEC volume would make `maxMint` follow a made-up number.

## D-WP7b-6 (2026-10-03, WP-7b) — release study inputs and rounding

**Decision.** The release tip comes from `env.data["release_tip"]`, then `policy.release_tip`, then the
rc1 tip 3,052,055 recorded in `params.cpp` (2026-10-02); the next upgrade from
`env.data`/`policy.next_upgrade_height`, else parsed from `chainparams.cpp` at the pin with `git show`
(none scheduled; recorded as `PINNED_UPGRADES` for runs without a clone). A start that misses the M14
lead moves to the next multiple of 1,000; the sunset is start + 420,480, capped at a scheduled upgrade.
Provenance is `judgement` (default tip) or `real-data` (supplied tip), never synthetic.

## D-WP7b-7 (2026-10-03, WP-7b) — design-note format and requested policy keys

**Decision.** Studies G3/G4/G9 expose `design_notes(results, policy) -> list[dict]` with keys
`id, title, finding, evidence, consequence, fix, params`, and attach to every Recommendation the notes
whose `params` include it, in `metrics["design_notes"]` (WP-8 deduplicates by `id`).
**Request to the integrator (optional policy keys; the studies read them with `getattr` and fall back
to the defaults shown):** `ensemble_agg` ("worst"), `dev_absence_tolerance_days` (0),
`yec_daily_volume_p10_usd` (none), `reference_price_usd` (none), `dust_spend_multiple` (3),
`residual_max_share` (0.01), `carrier_max_share` (0.001), `mint_inclusion_slack_blocks` (10),
`release_tip` (3,052,055), `release_tip_date` ("2026-10-02"), `next_upgrade_height` (none). G3 also
reads `claim_coverages`, `claim_stride_hours`, `claim_horizon_days`, `emergency_coverages`,
`emergency_open_hours` the same way (module `JUDGEMENT`). The `[agents]` keys are mapped onto
`AgentsConfig` by `g3_collateral.agents_from_policy`.

## Contract changes from WP-7b (2026-10-03, integrator)

The G3/G4/G9/release assumption keys are `Policy` fields (`[studies_g3_g4_g9]` in
`policy/default.toml`). `yec_daily_volume_p10_usd`, `reference_price_usd` and `next_upgrade_height`
default to None (unknown); TOML cannot express null, so they are documented as commented-out keys
and `test_default_toml_sets_every_field` accepts None-default fields documented that way.

## D-WP7d-1 (2026-10-03, WP-7d) — G6 judgement model and rule readings

**Decision.** REG-4 metrics come from a tag stream in which `PoolModel` pools mine blocks and quote the
12-block TWAP of *their own* `SpreadModel` exchange source (pool `i` → source `i mod 3`), so honest
deviation reflects source dispersion, staleness and outages; `stale-pools` maps its 30 % stale share and
10 % frozen share onto the pools whose shares sum closest. `reg4_deviations` returns the REG-4 deviation
(tested equal to `fees.judgement_series`). Rule readings: `deviationBps` minimises its own value (it is
the liar-detection threshold) under `≥ k_dev × p99`, false penalties and a ±20 % liar caught on ≥ 90 % of
its tags; `peerMin` is "the largest value with P(not evaluated) ≤ 5 %" literally (re-checked analytically
when `peerLag` moves); `accuracyBandBps` is the calm p75 rounded to 50 bps (rule family); liar detection
moves an honest tag against unchanged peers (the liar's own tags among the peers are ignored).
**Reason.** PLAN §5.6 names the metrics but not the noise model; tying quotes to sources lets a real
`spreads.csv` drive every judgement value through `SpreadModel.fit`.

## D-WP7d-2 (2026-10-03, WP-7d) — G6 fee model and the BLOCKED fallback

**Decision.** Revenue comes from one hour-mode vault book (a year from `startHeight`, policy personas,
MINT-6 refusals included) run with every G6 parameter at its registry value; FEE-1/AFEE-1 are recomputed
exactly per candidate on the same vaults (CRN). AFEE-1 is counted on every mint and claim (ARMED); pool
revenue is split equally over `expected_pool_count`, attestor revenue over `nSlots`; the attestor floor is
`max(attestor_min_monthly_revenue_usd, bondMin's monthly opportunity cost)` at the reference price
(G8's provisional 30,000 YEC is reported alongside). The fee share is the round-trip share of a `minMint`
vault's *debt*, ARMED, worst class. `feeBps × attestFeeBps` is one 2-D family; when no grid point is
feasible the verdict is BLOCKED at the point with the smallest sum of relative violations (ties toward
current), and a design note explains why.
**Finding (quick, synthetic).** FEE-1 on collateral makes class A's minMint round trip 2.81 % ARMED at
25/2,500 bps; attestors earn ≈ $22/seat/month at the low adoption case against a $50 floor; no grid
point satisfies both → BLOCKED.

## D-WP7d-3 (2026-10-03, WP-7d) — affordability, system tolerance, HALT-2 horizon

**Decision.** (1) "Redemption affordability under the 4·feeMin floor at crash prices" is read as: the
owner of a `minMint` vault (any class, minted at the reference price or at `worst_price_usd`) whose
collateral has fallen to the claim threshold still gains by redeeming, i.e. FEE-1 ≤ `1 −
10⁴/claimThresholdBps` of the collateral (9.1 %). At $100 a class-C minMint vault holds 3 YEC, so feeMin
0.5 YEC (16.7 %) fails and the verify rule moves it to 0.2 YEC. (2) The system bad-debt tolerance for
`globalRatioHaltBps` is `max_bad_debt_prob` weighted by each class's share of a mature book's outstanding
debt (class weight × mid-point term ≈ 1.6 %), and the risk is P(the price falls to 1/halt within
`grace`) — the time before any claim can act (fact 1.5-1).
**Reason.** The plan states both rules without a horizon or a weighting; these are the narrowest readings
that use only policy quantities. The class-weighted-by-arrivals alternative (1.0 %) can push the halt
to 27,500 on the GARCH placeholder and move `recapRatioBps` past class A's 50,000, closing class A too.

## D-WP7d-4 (2026-10-03, WP-7d) — supplyCapBps judged on cap-bound liquidation demand

**Decision.** "Liquidation volume" is the *demand*: collateral of vaults whose claim path is open and that
fall under `claimThresholdBps` at the true price, on their first such day, p95 over paths of the worst
crash day, worse crash. The cap rule applies the depth budget to the **cap-bound** (below
`recapRatioBps`) part; the cap-exempt class-A part is reported against the same budget and raised as a
design note. Inside the crash book `divergenceBps` is held at the registry value so the divergence sweep
reuses one book per (cap, halt). The p10 daily volume placeholder is $25,000 (judgement) until a depth
file is loaded.
**Reason.** W20 lets class A at σ = 1 bypass MINT-6, so PLAN §5.7's literal total-volume rule is
unsatisfiable by any cap whenever class-A demand alone exceeds the budget; a parameter can only be judged
on what it controls. The book's actual claimant sales are reported but not used (claims rarely pay,
WP-4 finding 3).

## D-WP7d-5 (2026-10-03, WP-7d) — HALT-2 timeliness and HALT-3 classification

**Decision.** HALT-2 is "timely" on a crash path if it fires (global ratio at xMint of a frozen mature
book below the halt) no later than the true system ratio reaches 150 %; the policy recall floor applies.
For HALT-3, positives are the falls of `crash-70-1d`, `crash-90-30d` and the dump of `pump-dump-3x`
(caught = fires between the fall's start and one day after its end); negatives are `calm-90d`, the part
of `pump-dump-3x` before the dump (the rally; HALT-3 fires on falls only, fact 1.5-6) and the 1-hour
`flash-wick-50-1h`; counts per path.
**Finding (quick).** At 2,000 bps HALT-3 catches only ≈ 40–60 % of the 30-day −90 % grind → recall below
0.9 → `divergenceBps` 1,500.

## D-WP7d-6 (2026-10-03, WP-7d) — requested policy keys

The G6/G7 judgement constants are module constants read with `getattr(policy, key, default)`; please add
them to `Policy` / `policy/default.toml` (a `[studies_g6_g7]` section): `liar_bias_bps` (2000),
`min_liar_detection` (0.90), `max_fee0_prob` (0.001), `min_registered_prob` (0.999),
`min_liar_exclusion` (0.99), `max_honest_exclusion` (0.01), `max_accuracy_sd_bps` (500),
`max_honest_payee_spread` (1.5), `min_accuracy_premium` (0.25), `p10_daily_volume_usd` (25000),
`system_ratio_alarm_bps` (15000). No frozen contract changed. `agents_config(policy)` (in
`g6_miners_fees`) maps the `[agents]` keys onto `AgentsConfig`; `AgentsConfig.from_policy` itself could
absorb that mapping (WP-4 file, not edited).

## D-WP7d-7 (2026-10-03, WP-7d) — excluded L6 values and payeeWindow

**Decision.** `payeeWindow`, `accuracyWindow`, `payeeTiltBps` and `nReg` are *verify* families (KEEP
unless a constraint fails — there is no cost to trade a shorter or longer window against);
`nPenalty` minimises honest exclusion subject to a smallest-share liar being excluded ≥ 99 % of the time.
FEE-W picks payees in proportion to tags in the window, so a pool's expected revenue share does not
depend on `payeeWindow`; the window is judged on FEE-0 only.

## Contract changes from WP-7d (2026-10-03, integrator)

G6/G7 assumption keys are `Policy` fields (`[studies_g6_g7]`). G7's liquidation budget now uses,
in order: a loaded depth file, the owner's `yec_daily_volume_p10_usd` (shared with G9), then the
`p10_daily_volume_usd` placeholder ($25k).

## D-WP8-1 (2026-10-03, WP-8) — joint pass: re-statement against the shipped set

**Decision.** Coordinate descent runs `run_group` per group on the current joint set and applies the
group's tunable recommendations through `recommended_set`. A group whose application would violate a
§1.4 invariant is *not applied*: the joint set is unchanged, and the outcome and warnings say why.
After the final round every Recommendation is restated:
- `current` becomes the shipped value and `recommended` the joint set's value (derived values
  follow their parent);
- KEEP becomes CHANGE when the value moved in an earlier round, and CHANGE becomes KEEP when it did
  not; PROVISIONAL and BLOCKED are kept;
- a value decided in round 1 and only confirmed later is reported with its round-1 Recommendation
  (metrics and explanation against the shipped value), plus a note;
- a value that moved in a later round keeps the final round's text, with the round-1 metrics "at
  the current value" and a note.

A study that cannot be imported, or has no `make_study`, is *not run*. A study that raises is
*error*. Neither stops the pass.
**Reason.** In later rounds a study compares against the joint set, not the shipped set, so its
own KEEP/CHANGE label and metrics no longer describe "shipped → recommended".

## D-WP8-2 (2026-10-03, WP-8) — the four top-level risk metrics (fast model)

**Decision.** `TopRiskModel` computes four metrics:
- **system P(bad debt)**: the mean over classes A/B/C of `metrics.p_bad_debt_fast`, with 4 terms
  per class and a 24 h start stride, on hourly paths. The paths are `min(32, max(4, paths/4))`
  GARCH-t preset paths, or a block bootstrap of real hourly data, over `hour_horizon_years`. Prices
  come from the ideal oracle kernel at 2 sub-steps, and medians are memoised per window, so ±1-step
  designs cost about 0.2 s per set.
- **price-halt hours/yr**: NO_PRICE or HALT-3 set in hour mode, after warm-up.
- **false activation-halt hours/yr**: the G5 analytic estimator at the policy's enforcing share.
- **oracle attack share**: the minimum over the three windows of the V16 exact binomial
  `min_attack_share`, at the policy tagging share.

Classes whose terms do not fit the horizon are left out of the mean.
**Reason.** PLAN §5.10 names these four. The study-grade evaluators are far too slow for a Saltelli
design. This model is for *ranking* sensitivity, not for deciding values.

## D-WP8-3 (2026-10-03, WP-8) — "insensitive" labels, it does not override

**Decision.** PLAN §5.10 says an insensitive parameter should "KEEP the current value". The joint
pass instead labels it (`rec.sensitivity["insensitive"]` and the joint sentence). It adds a note
when the label sits on a changed value, and it never changes the verdict.
**Reason.** The four system metrics do not read most G6/G8/G9 parameters at all (their total-order
index is structurally 0). Overriding would discard every study's own evidence for, say, `kSlack` or
`pinMinTags`.

## D-WP8-4 (2026-10-03, WP-8) — sensitivity design: ±1 step, grouping, admissible moves

**Decision.**
- Factors take the offsets −1/0/+1 registry step around the recommended value.
- When the Saltelli design `n·(k+2)` exceeds `20·budget.sobol_samples`, the factors are study
  groups, and one factor moves every parameter of its group by the same offset. At quick, that is
  72 parameters in 9 factors.
- Moves are coupled where a rule ties two values: `classMin[i+1] = classMax[i] + 1`, and
  `abandonBlocks ≥ grace`.
- A move that still fails an invariant is applied greedily, keeping each member only while the set
  stays admissible (for example, `mSelect + kSlack ≤ bundleMax`).
- The tornado is ±1 step per parameter, alone.
- Inside a sensitive group, a parameter is insensitive when its tornado share is below the
  threshold for every metric.

**Reason.** Without coupling and greedy repair, 80 % of the grouped Saltelli rows were inadmissible
(class contiguity, `vol_step_divides`, `bundle_size`). Per-parameter Sobol on the full set is out of
reach at quick. `ybcal sensitivity --params …` gives per-parameter indices on a subset.

## D-WP8-5 (2026-10-03, WP-8) — patch layout and the vendored params.cpp

**Decision.**
- `params.cpp.patch` holds the locked changes, the derived and per-release changes (the
  `MainParams()` start and sunset lines), and `attestMaxAge` (locked via k, D-3). It changes only
  lines inside `SetCommon()` / `MainParams()`. Each changed line gains a
  `ybcal: name old -> new (report §3.n)` comment, appended after any existing comment with `|`.
- Excluded field changes go to `params-patch-release.patch`.
- Header constants (`DEFAULT_REF_LAG`, params.h) are listed, never patched.
- Because `SetCommon()` is shared with testnet, `recommended.json`'s `test` column receives the same
  field changes, and its `regtest` column stays the shipped column.
- The pinned `params.cpp` is vendored as `params/params_cpp_7702d22.json` (text + sha256), so
  patches can be generated without a clone.
- `check_patch` runs `git apply --check` in `devnet.worktree.temp_worktree` under the work dir.

## D-WP8-6 (2026-10-03, WP-8) — PIN-1/PIN-2 fixed point in the engine (D-WP5-3 item 2)

**Decision.** `simulate_blocks` iterates attest, then PIN-1, then the medians, until the PIN-2
trigger mask computed from xMint stops changing, after at most `MAX_PIN_PASSES` = 8 passes. Pass 1
keeps its old behaviour (no pMint, so no PIN-2). Attestation reads pMint only through that mask,
and the coupled system is causal, so a stable mask is the unique solution. Only runs with
`inputs.attest` and `attest_mode="auto"` are affected. Hooks are not re-run. `extras["pin_passes"]`
and `["pin_fixed_point"]` record the result.
**Consequence.** No existing `tests/sim` result changed (no test runs the engine with attestation
and a moving pMint). `tests/sim/test_engine_pin_fixed_point.py` checks the fixed-point property
(attest re-run on the final xMint and the re-medianed prices both reproduce the engine), the
single-pass case and the pass limit.

## D-WP8-7 (2026-10-03, WP-8) — robust selection is left to the studies

**Decision.** PLAN §5.10 item 3 asks for robust (minimax-regret) final selection. The joint pass
does not run a second, cross-group robust selection. Each study already aggregates over its
scenario ensemble (`optimize.robust`), and ties break toward the current value through
`decide_with_materiality`.
**Reason.** A cross-group regret table needs every group's metrics on one scenario grid, which the
Study protocol does not provide.

## D-WP8-8 (2026-10-03, WP-8) — CLI wiring

**Decision.**
- `ybcal sensitivity` lives in `ybcal.optimize.cli` (the WP-6 route; resolves D-WP6-7). It takes
  `--set` (a report directory, a `recommended.json` or a ParamSet JSON; default the shipped set),
  plus `--params`, `--grouping`, `--workers` and `--out`.
- `ybcal study G` is a one-round joint pass over one group with a mini report.
- `ybcal recommend` adds `--groups`, `--workers`, `--ycash6`, `--max-rounds`, `--cache`,
  `--no-sensitivity` and `--sensitivity-method`, and replays a run with `--manifest`.
- `ybcal report open DIR [--serve --port]`.
- `--data` accepts directories, and each file's kind is sniffed from its header.

## D-WP8-9 (2026-10-03, WP-8) — runtime of the joint pass

**Finding.** Cache keys are digests of the whole set, so a group re-evaluates in round 2 whenever
any other group moved, even when it reads none of the moved values. With the four merged studies,
`recommend --budget quick` takes about 6 min on 4 cores (two rounds of 117 s, sensitivity 119 s).
Once G3/G4/G6/G7/G9 land, quick may exceed the 10-minute target. The owner then sets
`max_rounds_joint = 1` in the policy or passes `--max-rounds 1`, and the coupling is reported as
not iterated.
**Not done.** Projecting cache keys onto the parameters a study actually reads would need
instrumented ParamSets. That was judged too fragile to do here.

## D-WP8-10 (2026-10-03, WP-8) — rich design notes, BLOCKED rendering, late-round failures

**Decision.**
- **Design notes.** A design note is either a string or a dict `{id, title, finding, evidence,
  consequence, fix, params}`. That is the shape G3/G4/G6/G7/G9/release return, from module-level
  `design_notes(results, policy)` and from `Recommendation.metrics["design_notes"]`. The joint pass
  calls `design_notes(results, policy)`, or `design_notes(results)` for a one-argument helper. Dicts
  are de-duplicated by `id`, strings by normalised text. A note that begins "Design note:" in
  `Recommendation.notes` is also collected (G8's qLow note). §5 renders the title, id, groups and
  params, then the finding, evidence, consequence and fix.
- **BLOCKED parameters.** These get a box in the executive summary: current value, least-violating
  value, reason (the binding constraint plus the decision text), and the metrics at the
  least-violating value (`metrics["least_violating"]`, when the study gives them). The
  least-violating value is applied to the joint set like any other recommendation. Its patch line is
  marked `BLOCKED: least-violating value`, so the owner sees it was not a policy-feasible choice.
- **Late-round failures.** A group that succeeded in an earlier round but raises in a later round
  keeps its last successful outcome. Its Recommendations get a note, the joint set keeps their
  values, and the round history records the error.
**Finding.** In the quick run on all ten studies, G6's round-2 re-run on the joint set raised
`ValueError: cannot convert float NaN to integer` at `g6_miners_fees.py:908`. `dev_need` is NaN
when every honest-p99 sample is NaN (`np.nanmax` of all-NaN) once round 1 has moved
`peerMin`/`deviationBps`. This is a G6 bug (WP-7d), reported to the integrator. The report shows G6's
round-1 result with a note.

## G6 undefined honest p99 (2026-10-03, integrator)

`judge.dev_target` is NaN (like `acc_target`) when no honest quote is evaluated, instead of raising
on `math.ceil(nan)`; this was the round-2 joint-pass failure reported in WP-8.

## D-WP10-1 (2026-10-03, WP-10) — documentation integration; policy reference is tested

**Decision.**
- `docs/policy.md` documents every `Policy` field (meaning, unit, default, readers, guidance), and
  `tests/test_docs_policy.py` fails when a field has no table row or a row names no field. The
  "read by" column was taken from the code (`policy.<key>` / `getattr(policy, "<key>")`), not from
  the TOML comments.
- `docs/methodology.md` is the single reasoning document (WP-6's section kept as §7);
  `docs/architecture.md` gains an overview and contents, and its stale statements now describe the
  built system; this log gains an index; `docs/PLAN.md` gains an implementation status section.
**Findings (reported, not fixed: `src/` belongs to the owning WPs).**
- `diverge_spread_multiplier` is not wired: `g8_attestation.spreads_inputs` calls
  `_g8_ports.analyze_spreads` with its default `multiple=3.0`, so changing the key has no effect.
- `hour_kernel_tolerance_bps` (D-WP3-5 resolution) is not read by any study; the engine constant
  `KERNEL_TOLERANCE_P95_BPS` (same value) is what the kernel tests use.
- CI does not run `recommend --budget quick --synthetic` (PLAN §8); `make quick` is the manual
  smoke run.

## D-RD-D1 (2026-10-03, data) — fitted drift is neutralised by default

**Decision.** `synthetic.fit` (and `ybcal data synth --calibrate`) shifts every fitted model's drift
so its expected log return is zero (`--drift zero`, default; `--drift fitted` keeps the sample's),
and `BlockBootstrap.fit` returns a demeaned bootstrap. The regime switch keeps the difference
between its two states' drifts (crash regimes stay crash regimes); only the stationary mean moves.
**Reason.** On real data the fitted drift is noise that dominates: the CoinGecko year
2025-10-04..2026-10-04 rose 0.057 → 0.361 USD (+184 %/yr of log drift), so a 5-year bootstrap
or GBM fitted to it ended at the $100 `PRICE_MAX` clamp in the median, and G3 — which
bootstrapped real data without demeaning, unlike G1/G2/G6 — would have judged collateral safety
on a market that only goes up. Drift's standard error over one year at YEC's ≈ 230 % volatility
is ≈ 230 %/yr: not identifiable. Zero log drift (a flat median) is the no-view choice; a
martingale (zero arithmetic drift) at 230 % volatility would make the median fall 93 % a year,
against the 7-year record of ≈ −35 %/yr. Harsher paths are the scenarios' job.
**Consequences.** G3's real-data ensemble changes (now drift-neutral). The presets keep μ = 0
(arithmetic) as before.

## D-RD-D2 (2026-10-03, data) — long-history sources and the splice

**Decision.** `yec-hourly.csv` = CoinGecko's hourly year (the attestor's own aggregate) spliced
over CoinMarketCap hourly (2020-03-26 →); `yec-daily.csv` = CoinMarketCap daily spliced over
CoinCodex daily (2019-07-20 → 2020-03-25). Raw prices, no rescaling at the cut; the overlap check
and the splice-step return are recorded in each file's provenance. The pure single-source files
stay beside them.
**Reason.** The free CoinGecko plan serves 365 days; CryptoCompare now needs a key; CoinPaprika's
free plan serves one day of history. CoinMarketCap's public chart API and CoinCodex were the only
free long sources. Overlap: CoinGecko vs CoinMarketCap hourly median |diff| 68 bps, return
correlation 0.73 (hourly noise of two aggregators); at 00:00 UTC daily, 57 bps and 0.89.
**Consequences.** Hourly statistics before 2025-10 are CoinMarketCap's, which has stale runs
(D-RD-D4) and an unreliable volume field (zero or cents for long stretches): volume comes from
CoinGecko only. CoinMarketCap's endpoint is unofficial and may change.

## D-RD-D3 (2026-10-03, data) — reconstructed spreads stand in for the two-week log

**Decision.** Until `spreads.py log` has run two weeks (started 2026-10-03, `spreads-live.csv`),
`spreads-reconstructed.csv` (same columns) is built from hourly data: `coingecko` = CoinGecko's
hourly point; `safetrade`/`nonkyc` = the venue's last trade at or before it (close of its last
hourly candle with volume > 0), which is what the attestor's `coingecko_ticker` (SafeTrade
`converted_last`) and `nonkyc_market` (`lastPriceNumber`) sources read. A second file applies the
attestor's `max_age = 3600` to the venues.
**Reason.** The spread model and `divergeBpsAttest` need a distribution now; the venues' own candle
histories are the attestors' inputs, one hour apart instead of five minutes.
**Consequences.** Biases (docs/real-data-2026-10.md §4): hourly sampling (fewer, but identically
distributed, points), CoinGecko's hourly point is itself a smoothed aggregate, venue last trades
include stale quotes the live agent would still read (SafeTrade traded in 55 % of hours last
year). The live log supersedes it once it holds two weeks.

## D-RD-D4 (2026-10-03, data) — stale runs, regime embedding and GARCH at its bounds

**Decision.** (1) Fits drop runs of ≥ 6 exactly-zero returns and the return that closes each run.
(2) The regime switch maps its fitted discrete chain to CTMC rates through the exact two-state
embedding and simulates with `exp(Q dt)`. (3) A GARCH fit on its bounds (α = 0.5, ν → 2,
α + β → 1) is flagged and `data synth` warns; the bootstrap is the recommended real-data generator.
**Reason.** (1) CoinMarketCap held YEC at one price for 359 h in 2025: the GARCH likelihood
collapsed to ω ≈ 2·10⁻¹¹ (realised vol 0.1 %). (2) The per-state map `q = −ln(1 − p)/dt` is
exact only when switching is rare per step; YEC's hourly fit switches every few hours, and the
mismatch left +1.4/yr of log drift (median 5-year price $94 from $0.36). (3) Every YEC GARCH(1,1)-t
fit (hourly 1 y, hourly 6.5 y, daily 7 y) lands at α = 0.5 and α + β ≈ 0.9999 with ν ≈ 2.3–3: the
data leave the model family (excess kurtosis 48 hourly, negative lag-1 autocorrelation −0.19).
**Consequences.** Fitted models differ from the pre-M7 code on any data with these features.


## D-RD-D5 (2026-10-03, data) — data-informed policy keys

**Decision.** `policy/real-data-2026-10.toml` = `policy/default.toml` with four keys set from the
M7 data (docs/real-data-2026-10.md §10): `yec_daily_volume_p10_usd = 700` and
`p10_daily_volume_usd = 700` (CoinGecko daily 24 h volume p10 over 2025-10-04..2026-10-04: $718),
`expected_pool_count = 4` (payout keys ≥ 5 % of 70,000 blocks: median 4 per day, minimum 3;
effective number 2.9), `expected_enforcing_share = 0.70` (ninjaraider 52.0 % + mining-dutch
13.7 % + dapool 6.1 %). `policy/default.toml` is not changed: the integrator chooses.
**Reason.** The defaults were placeholders: the volume placeholder (25,000) overstated liquidity
35×; six tagging pools and 80 % enforcing hash assume a broader pool landscape than mainnet has.
0.70 is what the three identified operators deliver; 0.80 also needs an unidentified 21 % payout
key. `diverge_spread_multiplier` stays 3.0 (the data change its input: worst-pair p95 1,906 bps
reconstructed, so `divergeBpsAttest` ≈ 5,800 bps, provisional until the live log has two weeks).
`attack_share_min` is a goal, not a measurement, and is left alone — but the measured top-1 share
(0.52) exceeds it, which the owner should see.
**Consequences.** G5 should also be read at 0.48 (the ceiling without the top pool); G7/G9 depth
checks become binding at ~$70/day of liquidation volume (`max_depth_fraction` × $700).
## D-RD-AUD-1 (2026-10-03, audit) — one drift convention for solvency ensembles (centred)

**Finding.** The G3/G4 hour ensemble mixed drift conventions: `gbm`, `merton` and `regime` are
martingales (expected log drift −0.72, −0.69, −0.72 a year: the median price falls ~50 % a year),
`garch` is centred (0), and a real-data block bootstrap kept the sample's own drift (the year to
2026-10-04 on Coinpaprika: $0.062 → $0.361, +1.77 a year of log drift, 200 % daily volatility).
D-WP2-5 had removed exactly this bleed from every scenario base ("an uncentred 120 %-vol GBM adds
−72 %/yr of log drift, which would turn every scenario into a bleed"), and G1/G2/G6/G7 all centre,
but G3 called `SY.preset(name).simulate` directly. Over 1–5-year terms the drift, not the
dispersion, decided the class ratios: class C P(bad debt) at 600 % was 53 % martingale vs 20 %
centred (quick, seed 20261003); with real data the bootstrap's +177 %/yr would have driven every
ratio to its lower bound.
**Decision.** New policy key `price_drift` (`"centred"` default | `"martingale"` | `"model"`),
implemented in `ybcal.sim.drift` as a deterministic shift of each member's log returns (same draws,
so common random numbers and `"model"` reproduce the old paths exactly). G3/G4's ensemble and the
joint top-risk model read it; the real `history` member keeps its realised drift (it is what
happened, not a model). Default **centred**: a probability of a log-price threshold should not
embed a directional view, and the project convention (D-WP2-5) is centred. `martingale` is kept as a
stress run; at YEC-like volatility it makes every multi-year class look near-certain to fail.
**Consequence.** Verdicts are unchanged in kind — B and C stay BLOCKED, A moves to ≈ 725 % — but the
numbers shown to the owner are no longer an artefact: C at 600 % is 20 % (centred) instead of 53 %.
The worst member for class A is now the regime switch (its turbulent state carries −3.5/yr of log
drift by design: a crash regime).

## D-RD-AUD-2 (2026-10-03, audit) — the summary quotes G3's P(bad debt), not the fast model

**Finding.** The executive summary's second risk quoted the joint pass's top-risk model ("class C
9.47 % vs 2.0 %") while the BLOCKED box above it showed G3's 47.8 % for the same class at the same
ratio: the fast model uses one GARCH-t member (centred), four terms and a thinner start grid.
**Decision.** `report.build.top_risks` quotes G3's aggregate `pbad.{c}` at the recommended ratio
(`g3_bad_debt_over`) and names its source; the fast model is the fallback only when G3 did not run,
and is then labelled "GARCH-t only".

## D-RD-AUD-3 (2026-10-03, audit) — P(bad debt) at claim opening stays the constraint; severity and reach are evidence

**Question.** Is "P(collateral < debt when the claim path opens)" the right bad-debt definition?
Checked against ycash6 `a862a8a06`: the vault script (`script.cpp:79-93`) is `IF <lockHeight> CLTV
<owner> CHECKSIG ELSE <lockHeight + grace> CLTV TRUE ENDIF` and RED-2 (`state.cpp:515-517`) makes
every spend burn the full debt, so (1) nobody — owner or claimant — can act before `lockHeight`,
and no claimant before `lockHeight + grace`; (2) a vault under water is never claimed (the
claimant would burn more YED than the collateral is worth) and its owner walks away; (3) HALT-2
(`state.cpp:1237`) and the soft supply cap (MINT-6, W20) only stop *new* mints — no rule pools
collateral across vaults or lets a YED holder redeem against the system. So the YED behind a bad
vault has no burner: holders bear the gap through the peg.
**Decision.** Keep the policy's definition as the constraint: it is the first moment any rule could
act and the event after which the gap is the holders'. It is pessimistic in one way (a bad vault
stays claimable and can recover) and optimistic in another (it ignores severity). Both are now
reported: `es.{c}` = E[max(0, 1 − value/debt)] at claim opening (severity; `FastBadDebt.shortfall`)
and `tmax_ok_days.{c}` = the longest grid term within tolerance (how far the class reaches at that
ratio). G3-DN1 quotes both at the upper ratio bound: at quick/synthetic, B and C meet their
tolerance for **no** grid term even at 700 %/600 %, A only up to 71 days, and a bad C vault is short
by 58 % of its debt. That is a real property of the design (no liquidation during the term on a
100–200 %-volatility collateral), not a modelling artefact; the levers are the owner's: relax
`max_bad_debt_prob` for B/C, shorten `classMax[1]`/`classMax[2]` (parameters), or a rule change.

## D-RD-AUD-4 (2026-10-03, audit) — a RED-4(b) closure counts only when the exit pays

**Finding.** The emergency study counted every RED-4(b) trigger as a closure "by a YED holder exiting
at par" (D-WP4-6), although RED-5 under (b) pays that claimant the debt's worth at pClaim, the
higher price in a crash (mean loss 2,134 bps of the debt), and the policy's own market assumption
is a working peg (`yed_premium_bps = 0`). Crediting closures nobody would execute made the uncovered
debt fall monotonically in `emergencyRatioBps`, so the rule always picked the top of its range
(10,900, one step under θ).
**Decision.** A (b) closure happens at the first persisted trigger hour at which
`(1 − true/pClaim)·10⁴ + claimant_slippage_bps ≤ −yed_premium_bps`. The same metric under a 20 %
YED discount (`emergency_stress_premium_bps` = −2,000, a JUDGEMENT constant) is reported as
`emerg.shortfall_stress`.
**Consequence.** At the default policy (b) changes the uncovered debt by < 1 pp at any e, so
`emergencyRatioBps` is KEEP 10,500 (PROVISIONAL); the owner sees the depeg case beside it. G3-DN4
already records the rule-level fix (pay (b) at pEmerg or with a bounty).

## D-RD-AUD-5 (2026-10-03, audit) — `supplyCapBps` moves only for admitted demand and an evidenced depth bound

**Finding.** The synthetic report moved `supplyCapBps` 15 % → 50 % (the top of the grid) with
"improves primary metric by 233 %": the primary was the cap value itself, so any feasible larger cap
was an "improvement", and the depth constraint was vacuous. In the joint set every class ratio was
≥ `recapRatioBps` (500 %), so the W20 soft cap bound no class (`liq.demand_bound_usd` = 0 at every
cap). Standalone at the shipped ratios, the 50 % cap admitted $40 k of B/C debt of which only 13 %
opens its claim path inside the 365 + 60-day book: every class-C claim and the long B ones are
censored, so the measured cap-bound demand ($1.6 k against a $2.5 k budget) proved nothing. The move
also contradicted the owner's W20 decision (`SUPPLY_CAP_BPS` stays 1,500).
**Decision.** (1) Primary = `cap.bc_refused` (share of class-B/C mint attempts refused by MINT-6 in
the first year, minimise; ties toward current): a larger cap is a benefit only when it admits
demand, and materiality applies to that benefit. (2) `depth_bound` holds only when the cap-bound
debt is zero or at least `cap_min_uncensored_share` (0.5, JUDGEMENT) of it opens its claim path in
the book; otherwise it is *unverified* and counts as violated. (3) When no swept cap has any
cap-bound debt, the family keeps the current value ("uninformative"). (4) When the *current* cap's
bound debt is itself mostly censored, the book can neither confirm nor refute it: KEEP
("unverifiable") — added after the standard-budget seed check, where (2) alone ratcheted the cap
down to 250 bps (a cap admitting nothing) on one seed and BLOCKED it on another.
**Consequence.** Quick/synthetic standalone: KEEP 1,500 (B/C stay refused 91 % at 15 % and at 20 %;
the early-cap design note, fact 1.5-2, carries the real issue). A real depth file and a longer book
are what can justify a different cap.

## D-RD-AUD-6 (2026-10-03, audit) — HALT-3 calm hours share the availability budget

**Finding.** `divergenceBps` maximised a per-path F1 with a recall floor that includes the slow
`crash-90-30d` (its downward jumps are what HALT-3 catches); false positives came from a
`calm-90d` base of GBM 60 % — a third of YEC's realised daily volatility (200 % for the year to
2026-10). A tighter band (2,000 → 1,500, or 1,000 in the joint run) therefore cost nothing in the
synthetic calm, while on YEC's real price it would halt minting often.
**Decision.** New constraint `calm_availability`: HALT-3 hours per year in `calm-90d` ≤
`max_no_price_hours` — HALT-3 stops minting like NO_PRICE, so it draws on the same availability
budget. The recall definition is kept (catching the downward jumps of a slow crash is legitimate).
**Consequence.** No change on synthetic data (0 h/yr at every value); with real data the calm base
becomes a bootstrap of real returns (G1 `realise`) and the constraint can bind. The synthetic
recommendation (1,500) stays PROVISIONAL and rests on the 60 %-vol calm placeholder.

## D-RD-AUD-7 (2026-10-03, audit) — a BLOCKED `sigmaMultMaxBps` reports the bound and what drives the need

**Finding.** The synthetic report's BLOCKED cap ("p99 turbulent multiplier 5.28× exceeds the search
bound 50,000") listed the *current* 30,000 as the least-violating value, unlike every other BLOCKED
rule. The need itself was a joint-pass artefact: standalone (pFastWindow 96, volStep 48) the p99
turbulent multiplier is 4.19× (→ CHANGE 42,500); after G1 moved pFastWindow to 48 and G2 volStep to
24 the noisier σ̂ read 5.28×. In the placeholder turbulent Merton process one 40 % jump inside a
2-day window alone reads as σ̂ ≈ 480 %, so the p99 measures jump size, not sustained volatility.
**Decision.** Keep the PLAN §5.2 rule; a BLOCKED cap recommends the bound (50,000) as the least
violating value, and the recommendation's notes state the p99 σ̂ at the current vs chosen windows,
the single-jump caveat and the K12 cost of a high cap (an undefined sample sets the multiplier to
the cap, so every K12 trap then asks for cap × base ratio).
**Consequence.** With D-RD-AUD-9 the joint pass keeps pFastWindow at 96, so the synthetic need is
back to ≈ 4.2× (CHANGE to 42,500, PROVISIONAL). Real data decides: the turbulent σ comes from a
regime fit to real returns.

## D-RD-AUD-8 (2026-10-03, audit) — `peerMin` must hold at the participation floor

**Finding.** `peerMin` = "the largest value with P(not evaluated) ≤ 5 %" at the expected 80 %
tagging share moved 5 → 12 (19 peer blocks at peerLag 10, quote density ≈ 0.8). Minting keeps
running down to the participation floor (`participationFloor/signalWindow` = 60 %); there the
density is 0.6 and P(Bin(19, 0.6) < 12) ≈ 42 %: REG-4 would stop judging most tags exactly when
participation is weakest.
**Decision.** New constraint `not_evaluated_floor` on the `peer_min` and `peer_lag` families: the
analytic P(not evaluated) at the density scaled to the participation floor
(`floor_density = density · min(1, floor/expected_enforcing_share)`) must also be ≤
`max_not_evaluated_prob`; `adjust_changes` re-checks peerMin at that density when peerLag moves.
**Consequence.** quick/synthetic: peerMin 5 → 8 (PROVISIONAL; P(not evaluated) at the floor 4.0 %) instead of 12. A real pool-share log
sets the density; few pools with one dominant change the picture further (a dominant pool's own tags
fill its peer window — modelling limitation, see the audit report).

## D-RD-AUD-9 (2026-10-03, audit) — forced moves are minimal; G1 reads real pool shares

**Finding.** G1 moved `pFastWindow` 2 h → 1 h *and* `pMidWindow` 12 h → 1 d because the current set
violated `max_no_price_hours` (20.5 h/yr vs 6) and `decide_with_materiality` then jumped to the
best-J feasible set. Only pMid fixes the violation: (96, 1,152, 2,016) has 2.2 h/yr and J 1.50 vs
the best (48, 1,152, 2,016) at 4.2 h/yr and J 1.28 — within materiality, so the extra pFast move
rested on a 15 % J gain that would not have cleared materiality on its own, and it is what pushed
G2's σ̂ cap need to 5.28× (D-RD-AUD-7). Separately, G1/G2 always used `expected_pool_count` equal
pools even when a pool-share log was loaded, although background NO_PRICE is set by the largest
pool's share against the ⌈2W/3⌉ fill: with the G6 placeholder shares (25/20/15/10/6/4 %) no window
set reaches 6 h/yr (best 18 h/yr at 192/1,152/3,024; shipped 46 h/yr).
**Decision.** (1) `decide_with_materiality`: a violating current moves to the feasible row closest
to current among those within materiality of the best feasible primary (minimal change, PLAN §2.3).
(2) `g1_price_windows.oracle_config` uses the tagging pools of a real pool-share log when one is
loaded (`g6.pool_model`), else the policy's equal pools; `realise` is keyed on the pool shares.
**Consequence.** quick/synthetic G1: pFastWindow KEEP 96, pMidWindow 576 → 1,152 (PROVISIONAL). The
longer pMid is robust across tagging shares 0.76–0.90 (it rides out single-pool feed outages); its
cost is slower recovery after an all-feeds outage (26.5 h vs 16.5 h per 6-hour outage). With a
concentrated real pool landscape G1 may turn BLOCKED on `max_no_price_hours`: that is a real
availability risk (the largest pool's feed uptime), not a window choice.

## D-RD-AUD-10 (2026-10-03, audit) — heterogeneity is not a constraint; neighbours name the rule's own constraints

**Finding.** G3 put `het_{A,B,C}` (within-class term heterogeneity, a design-note trigger) into every
candidate's constraints, and the report's "why not the neighbours" listed every violated constraint
of the whole set: `emergencyRatioBps` −1 step "violates het_A, bad_debt_B, het_B, bad_debt_C,
het_C" — none of which its rule reads.
**Decision.** Heterogeneity stays a value (`het.*`, `viol.het_*`) and drives G3-DN6 only.
`report.explain.neighbours_text` names only constraints that appear in the recommendation's own
`constraints_current`; a neighbour failing only other rules' constraints is compared on the primary.

## D-RD-AUD-11 (2026-10-03, audit) — `volStep ≥ pFastWindow / 2`

**Finding.** At the standard budget (seeds 1, 2) G2 minimised the calm CV of σ̂ by taking the
smallest step on the grid (volStep 12, volWindow 3,456) and then calibrated `sigmaRefBps` to 3,500–
4,000. σ̂ samples pFast, a rolling median over `pFastWindow` (96) blocks; sampled every 12 blocks
its increments overlap and are smoothed, so the K13 annualisation (iid increments) measures the
median filter, not the price: σ̂ falls with the step and its CV falls for the wrong reason. The
low reference then inflated every multiplier and BLOCKED the cap.
**Decision.** New G2 constraint `sampling`: `volStep · 2 ≥ pFastWindow` (`g2_volatility.sampling_ok`).
The shipped pair (48, 96) sits exactly on it.

## D-RD-AUD-12 (2026-10-03, audit) — quick-budget verdicts are not lock-grade: seed and policy robustness

**Evidence** (standalone studies at the shipped set, synthetic, `.work/seeds.py`, `.work/g3pol.py`):
- *Stable across seeds 1–3 (quick) and 1–2 (standard):* B and C BLOCKED (700 % / 600 % least
  violating); `emergencyRatioBps` KEEP; `peerMin` 8; `qLowBps` 3,500; `dormancyMinBundles` 12;
  `walletConfirmations` 24; `bondMin` 30,000 YEC; `divergeBpsAttest` 1,100; `nPenalty` 144;
  `accuracyBandBps` 100; the fee pair BLOCKED at the shipped ratios (low-adoption attestor revenue
  $22–26/month vs the $50 floor with a 2 % fee-share cap).
- *Unstable at quick:* `baseRatioBps[0]` 725 % (seed 20261003) vs BLOCKED at 800 % (seeds 1–3) —
  standard gives 775 %/800 %; G1 windows (seed 2 chose 48/864/1,152); G7 cap/halt/divergence;
  G2 window/step (fixed by D-RD-AUD-11).
- *Policy sensitivity of class A* (quick, default seed): `ensemble_agg = mean` → 525 %;
  `sigma_mult_at = p90` → 525 %; `term_distribution` short-heavy → 625 %, long-heavy → 800 %;
  `price_drift = martingale` → BLOCKED. B and C are BLOCKED under every one of these.
**Decision.** No code change: the quick budget is a smoke run. A value is lock-grade only from a
standard (or deep) run whose verdict is the same on at least two seeds; class A's ratio is decided
by the worst member (the regime-switch preset) and by three owner policy choices, which the owner
must confirm before it is locked. Recorded for the integrator's real-data runs.


## D-RD-DEV-1 (2026-10-03, devnet) — the devnet on a shared machine: port band, reuse of a built tree, teardown

**Decision.** `--port-base B` / `$YBCAL_DEVNET_PORT_BASE` keeps a devnet in `B … B+999` (seed `s` =
24 ports: p2p `B+24s+n`, RPC `B+24s+12+n`); busy ports are refused before a node starts; nodes bind
127.0.0.1 only. `ybcal devnet build` borrows a built clone's `depends/<triple>` (symlink) and cargo
`target/` (copy-on-write clone) and runs `autogen` + `configure` instead of `zcutil/build.sh`; the
build environment pins `CARGO_TARGET_DIR` to the worktree and puts GNU libtool/coreutils first.
Run dirs carry a random suffix; SIGTERM stops a run's nodes. `wait_synced` accepts the index's
`height: -1` below `startHeight`.
**Reason.** As shipped the harness could not run here: the framework port scheme cannot reach the
41000–41999 band other agents' nodes leave free; start-up waited for index height 0 at genesis
(which the node reports as −1) and failed after 120 s; a build would have run the 35–60 min cold
`build.sh` with BSD tools and the profile's shared cargo target; two suites started in the same
second shared run directories.
**Consequence.** A first worktree build takes ~4 min (ycash6) / ~7 min (ycash-dd), a later overlay
build ~10 s – 2 min. The clones themselves are only touched by `git worktree add --detach` /
`remove` (verified: `ycash6` and `ycash-dd` stay on their branches, nothing committed).

## D-RD-DEV-2 (2026-10-03, devnet) — miners follow one plan across the schedule (scenario realism)

**Decision.** `MinerPlan` (smooth weighted round robin with exact fractions) assigns every block's
miner across the whole schedule; the replay and `engine.devnet_inputs` both use it. New
`ReplayStep.frozen_pools` repeats a pool's previous quote (a stuck feed).
**Reason.** `block_miners` interleaved within one step. Price walks are one step per block, so pool 0
mined every such block: `oracle-attack-34` ran a 100 % attacker, `hashrate-drop` 100 %/0 %
signalling, and the median of three pools was never exercised. The differential still passed (both
sides used the same wrong schedule), so the bug was in the scenarios, not in the simulator.
**Consequence.** All results in `docs/devnet.md` "Validation results 2026-10" are from runs after the
fix. Seeds of earlier runs do not reproduce the same chains.

## D-RD-DEV-3 (2026-10-03, devnet) — vault differential: replay the node's transactions through the rule layer

**Decision.** For runs with wallet actions the node's transactions (MINT height, refHeight, amount,
lock, collateral; spend path and refHeight) are inputs; the simulator re-derives verdicts, statuses,
closes, burns, fees, per-height supply/collateral (hence `globalRatioBps` and HALT-2), per-height
`claimable`, the wallet's collateral and the verdict for every refused mint, and these are compared
exactly. ARMED transactions take their bundle from the simulator's own attestation replay.
**Reason.** The vault book's agents are stochastic; a deterministic comparison needs the same
transactions on both sides. What the rules decide from them is what calibration relies on.
**Consequence.** The simulator's own *timing* model of when a wallet transacts is not tested by this
(D-RD-DEV-7, L-1).

## D-RD-DEV-4 (2026-10-03, devnet) — emulated attestor seats instead of the Rust agent

**Decision.** The minimal backend registers `max(3, attestArmMin)` seats round-robin on the pool
nodes and emulates `yellowback-attest attest`: every `attestInterval` cited heights
(`cited = tip − REF_LAG`, phase = seq) a live seat signs the step's price with
`yed_signattestation` and every node receives it with `yed_addattestation`. `attestors_down` stops
a seat; the `freeze` action makes a seat re-sign one price (PIN-2). `attestor-outage-1` and `pin`
run on the minimal backend.
**Reason.** The launcher's port scheme cannot be confined to a port band and it hard-codes the
runtime flags (D-WP9-4); the agent's cadence (`attest.rs`: every N blocks, `cited = height −
ref_lag`) is simple to reproduce, and the replay then knows every signature exactly (WP-5's
`sign_heights` / `sign_prices`).
**Consequence.** Not covered: real price sourcing and aggregation (L5 failed polls), transport,
REV-1 (the emulated agent never revives: a DORMANT seat stays DORMANT), equivocation, bond
withdrawal. These remain functional-test territory (ycash6 `qa/rpc-tests/yellowback_attest*.py`).

## D-RD-DEV-5 (2026-10-03, devnet) — two simulator fixes the devnet found (attestation frame, dormancy record)

**Decision.** (1) `BlockSeries.height0` (= `start_height`): `attest.simulate` and
`engine._attest_frame` read `series.height0` and fell back to 0, so an engine run whose attest dict
had no `height0` walked the attestation layer — and the PIN-2 trigger mask — one block behind the
engine's columns (a bundle in the last block found "no snapshot"; the devnet's 21st bundle of
`attestor-outage-1` diverged). (2) `attest.simulate(record_status=True)` now records DORMANT from
the dormancy height itself (post-SNAP), as `yed_listattestors` reports it; the transitions were
already right.
**Reason.** Both were found by comparing with the nodes; the reference-model tests compared
transitions only.
**Consequence.** No current study is affected: G8 calls `attest.simulate` directly with `height0` set
and reads transitions, and no study runs the engine with attestation inputs. A future joint ARMED
run would have been shifted by one block. `tests/sim/test_attest_reference.py` follows (2).

## D-RD-DEV-6 (2026-10-03, devnet) — binaries and skew for the 2026-10 validation

**Decision.** ycash6 runs use a binary built by `ybcal devnet build` at the pin `7702d22`
(`v6.21.0-rc1-7702d2260`), not the workspace's `ycash6/src/ycashd`, whose banner is `94bafa4fd-dirty`
and whose mtime (2026-10-02 21:13) predates W19/W20/W21 (23:36–00:14). ycash-dd baseline runs use
`ycash-dd/src/ycashd` (`v4.5.0-cdfc4945f-dirty`, built before W20 landed on ycash-dd) under
`--allow-version-skew`; its overlay runs use a binary built from ycash-dd `HEAD` (`f78a5f8`, W18–W21
included) by the same tooling.
**Reason.** The stock regtest column runs with `supplyCapBps = 0`, so W20 is inert there and the
pre-W20 ycash-dd binary is a fair baseline; a scaled mainnet overlay carries a 1,500 bps cap, which
needs W20.
**Consequence.** Both lines validated on both the shipped regtest column and the scaled shipped
mainnet set (`docs/devnet.md`).

## D-RD-DEV-7 (2026-10-03, devnet) — what remains unmodelled, and which studies it weakens

**Decision.** Recorded as limitations, not fixed:
- **L-1 transaction timing.** On the node a MINT confirms 4 blocks after its R (carrier at tip+1,
  MINT at tip+2, R = tip − 2), an owner redemption 1 block after R (R = tip), a claim 2 blocks after
  R (R = tip). The vault book's block mode uses R = step − 3 for mints and owner spends. The
  difference is 1–2 blocks (≤ 2.5 min on mainnet) against a 96-block fast window: it moves no G3/G4
  verdict measurably; claim-race studies at the minute scale would need it.
- **L-2 claimable under ARMED.** `yed_listvaults.claimable` under ARMED builds a bundle per vault;
  the differential compares claim *verdicts* under ARMED but the per-height claimable flag only on
  unarmed heights.
- **L-3 abandonment and notices.** `abandoned`, sweeps and NOT-1 emergency notices are not driven by
  a scenario (the scaled-mainnet `hashrate-drop` does reach abandonment — `abandonBlocks` = 24 at
  `--term-factor 1440` — but holds no vault). G4/W21 abandonment heights rest on the reference-model
  tests only.
- **L-4 σ at regtest scale.** With the shipped `sigmaRefBps` the regtest σ estimate saturates the
  multiplier (two values in a run); σ was exercised with `sigmaRefBps = 100000` and in `pin` under
  the scaled mainnet set (27 distinct multipliers). The scaled set's σ is not a calibration input.
- **F-DEV-1 (node wallet, both lines; reported, not a rule divergence).** A MINT spends its
  carrier's change output; the wallet does not mark that output spent, so the next carrier or
  `yed_send` within a few blocks can select it and is rejected by the mempool ("transaction commit
  failed"). Seen in 1–10 of ~20–30 mints per run on ycash6 and ycash-dd. It affects wallet UX
  (back-to-back mints), not consensus or calibration.
**Consequence.** Studies weakened: claim-timing races (L-1), ARMED claimability on the minute scale
(L-2), abandonment/sweep timing (L-3). G1/G2 (oracle), G3/G4 (vault solvency, claims), G5
(activation), G7 (supply) and G8 (attestation liveness, dormancy, PIN) rest on rules the devnet now
matches exactly on both lines.

## D-RD-INF-1 (2026-10-03, infra) — price inputs by role; observed-to-observed returns everywhere

**Decision.** `load_data` no longer lets the last price file win. Every price CSV is read first and
assigned a role by its **native granularity** (modal spacing of its own timestamps; ≥ 20 h is
daily), never by `--data` order (`ybcal.data.inputs`): the finest series is `price` (what every
study reads), a daily series next to it is `price_daily` (for long-horizon consumers: G3 class C
5-year terms, G4, drawdown evidence; `g1_price_windows.long_price(env)` returns it, falling back to
`price`). A single price file is `price` whatever its step. Two series competing for one role are
an error. Both sit on the hourly grid with the loader's `filled` mask; the report's data table names
the role and native step (`price [price_daily, native 1 d]`), and `data_fingerprint` hashes both.
Two places that lost the mask are fixed: G3's `_real_hourly` rebuilt the path without it (so a daily
series on the hourly grid showed 23 exact-zero returns a day, and `drop_stale_runs` emptied it —
the `rd0` "empty return series" in G3/G4), and `pricepath.resample(→ block)` repeated the hour mask
over all 48 blocks (so an hourly path on the block grid fitted 47 zero returns per hour and was
emptied the same way); the 47 held blocks are now marked filled, and block → hour returns the
original mask exactly.
**Reason.** The wave-1 run passed `yec-hourly.csv` and `yec-daily.csv`; the daily file won, the
stale-run filter emptied it, and G3/G4 did not run. Long-horizon studies want 6.5 years of daily
history (σ ≈ 236 %/yr daily) next to the hourly microstructure (σ ≈ 444 %/yr hourly, lag-1 ac −0.2).
**Consequence.** On the real files: `price` = 56,165 hourly returns, `price_daily` = 2,597 daily
returns; per window (`--window`, D-RD-INF-5) last365 8,760 / 364, 2021-22 17,502 / 729, 2025-26
14,516 / 605 — none empty. Studies that want the daily series opt in via `long_price`; until they
do, results with and without `yec-daily.csv` differ only in cache keys.

## D-RD-INF-2 (2026-10-04, infra) — owner-pinned parameters are studied and kept

**Decision.** New policy key `owner_pinned` (param → decision reference), applied in
`optimize/runner.run_group` by `optimize/pins.apply_owner_pins`, so `recommend`, `study` and the joint
pass all honour it: (1) the study decides on its full table — the *evidence*; (2) if that moves a
pinned value, the study decides again on the rows where every pinned value holds, so the group's
other parameters are chosen given the pin (the joint pass therefore treats it as fixed); (3) the
pinned Recommendation is `KEEP`, `recommended = current`, with `metrics["owner_pin"]` = {ref, kept,
evidence value and verdict, *because* (the evidence's binding constraint and decision), *risk of
keeping* (constraints failing at the kept value, primary metric kept vs evidence)}. The report
prints `KEEP (owner decision <ref>)`, an executive-summary table "Owner decisions the evidence
argues against", a callout at the top of the parameter section, a top-risk line, and a required
lock-readiness item "Owner-pinned parameters hold their decided values" (the real-data item no
longer requires data for a pinned value). In the joint pass a pinned parameter's evidence is the
final round's (read at the final joint set), not the round-1 one. Pins must name tunable registry
fields (`Policy.__post_init__`).
**The pins** (both `policy/default.toml` and `policy/real-data-2026-10.toml`; citations in
`docs/policy.md`): `abandonBlocks` W21/D-R-12; `grace` D-R-6 (reaffirmed W21); `classMin[0..2]`,
`classMax[0..2]` D-R-6/W21 ("GRACE and the lock classes stay as they are", v3 revision 4);
`supplyCapBps` W20/D-R-11; `attestFeeBps` D-3; `attestArmMin`, `attestArmDelay` D-4 (proposal §16);
`activationThreshold`, `participationFloor` L3 ("the mint halt keeps 60 % / 75 %", v2 revision 4);
`enforcementFloor`, `enforcementResume` L3 (1,008 / 1,210); `valveBlocks` L7 (v2 revision 5). Not
pinned because not tunable, honoured by derivation: `recapRatioBps` = 2 × `globalRatioHaltBps`
(W16/D-R-3), the window minimum fills (L9), the sunset span (L8). L4's launch bar (N ≥ 3 pools, none
above 40 %) is an operational gate, not a parameter; G5 reads it as context.
**Reason.** The owner's decisions are not open; a study that silently recommends changing one (rd2:
`abandonBlocks` → 90 d, `attestFeeBps` → 50 %, `valveBlocks` → 7) misleads, and one that drops the
evidence hides a risk. Re-deciding on the pinned slice of the same table costs no evaluation.
**Consequence.** Machinery tests that move pinned fields use `Policy(owner_pinned={})`. Group agents
read `rec.metrics["owner_pin"]` instead of overriding verdicts themselves.

## D-RD-INF-3 (2026-10-04, infra) — "policy unmeetable in this environment" is not BLOCKED

**Decision.** `ybcal.studies.envlimit.decide_with_environment(table, policy, limits, …)` is a drop-in
for `decide_with_materiality`. A study declares the constraints the real environment may defeat as
`EnvironmentLimit(constraint, harm_metric, minimize, note, why, exposure, title, fix)`. When the
ordinary decision is BLOCKED **and every constraint no evaluated row meets is a declared limit**,
it decides on the rows meeting all other constraints by the limit's least-harm objective, with the
policy materiality and minimal change (the nearest value within materiality of the best); the
verdict is KEEP/CHANGE (then `final_verdict` as usual) and `EnvDecision.environment` records the
constraints, why, the design note id, the least-harm metric at choice/current/best and the exposure
(a string or `row -> str`, evaluated at the chosen row). `attach_environment(rec, dec)` writes
`metrics["environment_blocked"]`, a design note per limit (id = the note, so §5 de-duplicates
across parameters) and a leading note. The report prints `KEEP|CHANGE — policy unmeetable in this
environment (design note N)`, an executive-summary table with the exposure, a callout in the
section and a top risk; lock-readiness counts it apart from BLOCKED, as a required item that passes
when every such parameter has its note and a quantified exposure. A constraint that is unmeetable
but not declared, or a mix with an undeclared one, stays BLOCKED.
**Reason.** On real data `attack_share_min` 0.34 cannot be met by any window while one pool mines
52 % of blocks, and `activation_reach` cannot be met when the enforcing share is below every
threshold; BLOCKED there hid the usable answer (the least-harm value) and blurred it with real
parameter failures. Whether a constraint is environment-limited is study knowledge, so the study
declares it; the mechanism is generic.
**Consequence.** The group agents (G1 attack share, G5 activation reach, G9 reorg share) opt in in
their `decide`; nothing changes for a study that does not.

## D-RD-INF-4 (2026-10-04, infra) — G9 on real data: no zero amounts; thin market and majority pool are environment limits

**Decision.** (1) An empty admissible range keeps the current value clamped into the registry
bounds; the BLOCKED fallback can no longer produce 0 (the rd2 crash: maxMint's depth bound $63.6
rounded onto the $1,000 lattice to 0, minMint followed, `fee_table` raised). `sim/fees.py`
distinguishes "undefined: non-positive input" from K14 (> MAX_MONEY), as ycash6 `math.h:111-124`
does internally. (2) maxMint: when no value ≥ max(minMint, registry floor) passes the depth check,
it is an environment limit (G9-ENV-1, D-RD-INF-3): KEEP the current value, exposure = the
liquidation in days of p10 volume and multiples of the ±2 % depth, frontier CSV in the evidence.
Least harm is the current value because a per-vault cap does not bound the aggregate liquidation
load (owners split vaults); the aggregate is G7/G3's. (3) walletConfirmations / DEFAULT_REF_LAG read
the real pool landscape (`env.data["pool_shares"]`): the adversary share is max(policy, real top)
when the top is below half; with a majority pool, z is sized against max(policy, second pool) and the
majority is an environment limit (G9-ENV-2). `G9Context` gains `top_share`, `second_share`,
`policy_reorg_q`, `depth_2pct_usd` (defaults keep old cached contexts loadable).
**Reason.** The briefing: battle-test G9 on the real depth ($169–183 at ±2 %, p10 volume $700) and
the real pool landscape (52 % top pool); a crash and a BLOCKED there hid a usable answer.
**Consequence.** On the real inputs: minMint $100 KEEP, maxMint $10,000 KEEP (environment-limited),
minOutput $1 KEEP, maxOutput $100,000 KEEP, residualMinZat 100,000 KEEP, carrierValue 10,000 KEEP,
walletConfirmations 6 → 24 CHANGE (environment-limited: the 52 % pool is unbounded), DEFAULT_REF_LAG
2 KEEP. G9 is closed-form; price data enters only through the reference price (minMint's fee-floor
bound), so seeds and price models cannot move it, windows can (robustness harness, D-RD-INF-5).

**Amendment (2026-10-04, integrator note).** L3 fixes *shares* of the signal window, so
`activationThreshold`, `participationFloor`, `enforcementFloor` and `enforcementResume` are pinned as
fractions: `{ ref = "L3", of = "signalWindow" }` keeps 75 / 60 / 50 / 60 % of whatever `signalWindow`
the study chooses (signalWindow itself is not pinned), e.g. 2,592 → 1,944 / 1,556 / 1,296 / 1,556. A
fraction pin is re-decided on the rows holding the fraction (±1 block); its verdict is CHANGE when
the parent moved, rendered `CHANGE (owner decision L3: 75% of signalWindow)`. The joint pass also
reads `metrics["environment_blocked"]` on a BLOCKED row (a study's own environment limit, e.g. the
valve) and re-states it as the least-harm KEEP/CHANGE (D-RD-INF-3).

## D-RD-INF-5 (2026-10-04, infra) — the robustness harness: windows, price models, policy overrides

**Decision.** `ybcal robust` runs `ybcal recommend` (a subprocess per combination, `nice` 10) over
seeds × `--window` (full / last365 / 2021-22 / 2025-26 / lastN / A:B, D-RD-INF-1) × price models,
each in `<out>/runs/<window>__<model>__s<seed>/`, and tabulates per tunable parameter: value and
verdict of every run (`(pin)` / `(env)` marked), modal value and agreement, verdict agreement, the
modal value per window / model / seed, and flags `unstable` (agreement < `--agree`, 0.8),
`window-sensitive`, `model-sensitive`, `seed-noise`. Bounded CPU (`--jobs` × `--workers`, warned if
above the core count); resumable (a run with `robust-run.json` exit 0 and the same command is
skipped); `--cache` shares the on-disk evaluation cache (its keys include seed, policy and data);
`--table-only` re-tabulates; `--dry-run` prints the commands. Price models: `bootstrap` (the
demeaned block bootstrap, default), `regime` / `garch` — new policy key `real_price_model`: that
model fitted on the longest real series (`price_daily` when given) and wrapped `Centred` (zero
expected log drift), routed through one helper `g1_price_windows.real_model(env, pp)` at every site
that bootstrapped real data (G1, G2, G3, G6 ×2, the joint risk model); `martingale` = `price_drift`.
`data_fingerprint` includes the model so memoised ensembles do not leak across models. Policy
overrides per run: `--policy-set KEY=VALUE` (TOML value; `--set` was taken by `sensitivity`) on
`recommend`/`study`/`robust`, recorded in the manifest and appended to the report's policy text,
re-applied by `--manifest`.
**Reason.** The briefing's "battle tested" bar: stable across ≥ 3 seeds, the four windows and the
three price models. Subprocesses keep runs isolated, killable and resumable; the long table makes
the final report a join over runs, not a re-run.
**Consequence.** A full `standard` sweep (3 × 4 × 3 = 36 runs) is hours of CPU; run it in a
resumable script (`.work/runs/`), group subsets (`--groups`) for a study agent's own parameters.

## D-RD-INF-6 (2026-10-04, infra) — report polish: BLOCKED reasons name the value; lock-readiness counts pins and environment limits

**Decision.** (1) When the joint pass re-states a BLOCKED recommendation whose least-violating value
differs from the shipped one, "least violating: current" in its binding, decision and notes becomes
"least violating: <value> (the joint set's value; shipped <x>)" (rd2: `baseRatioBps[1]` 70,000 was
reported as "least violating: current"). (2) The executive-summary BLOCKED reason de-duplicates its
parts (rd2 printed "no candidate satisfies the policy" three times for the G1 windows). (3)
Lock-readiness: an owner-pinned value needs no real-data backing (item 2), a required item checks
every pin holds its decided value or fraction (D-RD-INF-2) and names the pins the evidence argues
against; environment-limited parameters are not BLOCKED and have their own required item (note +
quantified exposure, D-RD-INF-3).
**Reason.** The audit: the re-stated text contradicted the value in the patch, and the checklist
counted owner decisions and unmeetable-environment constraints as parameter failures.

**Amendment (2026-10-04): the consolidation rule.** Per-run "smallest feasible" selection is
unstable (G5 standard on real pools: `signalWindow` 2,592 / 2,592 / 4,032 / 3,744 / 2,016), so the
harness also consolidates: for each parameter, every candidate of every run counts (the runs' final
result tables `evidence/<g>/results.csv`, not only each run's winner); a value is feasible in a run
when the parameter's own rule constraints (`constraints_current`, D-RD-AUD-10) hold at the row that
is closest to that run's recommended set on the group's other parameters (rows a pin excludes — e.g.
thresholds not at their L3 fraction of the window — are not candidates; fraction-tied parameters move
together); the **consolidated** value is feasible in the most runs, ties → closest to current, with
"feasible k/N" and, per run, the constraints failing there. Flag `none-feasible` when no candidate
meets its rule anywhere. `ybcal robust --runs DIR…` tabulates any finished `recommend` directories.
On the activation agent's five G5 standard runs: `signalWindow` 2,592 feasible in 3/5 (false_halt
fails in s3 and h1), thresholds 1,944 / 1,556 / 1,296 / 1,556 (3/5), `valveBlocks` none-feasible
(valve_minority in 5/5; environment-limited).

## D-RD-ACT-1 (2026-10-04, activation) — the enforcing coalition is named, and the real landscape is what it is

**Finding.** The rd2 quick report BLOCKED every G5 threshold with 1,001 false-halt hours a year.
That number was an artefact: `share_samples` took "the largest pools whose cumulative share is
closest to `expected_enforcing_share`", which on the real log is ninjaraider (52.0 %) + the
unidentified key s1jrMEF9 (21.3 %). That key mined nothing after block ≈ 3,043,700 (day 52 of 60),
when zpool.ca rose from 0 to 27–29 % of the blocks the same day: one ≈ 25 % block of hash
("flex") moved between payout keys, so the "coalition" lost a third of itself mid-sample.
The data agent's 0.70 is a different coalition: ninjaraider + mining-dutch + dapool (71.9 %).
**Environment facts** (data/local/pool-shares.csv, 70,000 blocks, `ybcal data landscape`):
- Without ninjaraider the rest of the chain is 48.0 %: no coalition without it reaches the L3
  majority, so **activation without ninjaraider is impossible at any threshold ≥ 50 %**, and a
  threshold below 50 % would activate a minority that sits in the ENFORCEMENT halt at once. Every
  activating coalition contains ninjaraider; it alone can switch enforcement off (by leaving) or fake
  it (by signalling without enforcing, scenario `real-pools-ninja-rogue`). No parameter changes this.
- A 2,016-block window's share of the identified coalition has sd 3.9 points (binomial: 1.0), min
  60.3 %, and is ≥ 75 % in 18 % of windows: lock-in at the shipped 75 % happens on day-scale
  excursions, not on the mean.
**Decision.** New policy key `enforcing_pools` (payout-key prefixes); the real-data policy names the
three identified operators. Without it the auto rule takes the largest operators until their share
first reaches `expected_enforcing_share`. `share_samples` (the synthetic-path mixture and G4's false
abandonment) uses the same coalition.

## D-RD-ACT-4 (2026-10-04, activation) — the activation threshold is the lock-in bar; spurious lock-in is a design note

**Finding.** With the real landscape and the old rule ("minimise false-halt hours subject to
reach"), the activation family drove `activationThreshold` from 75 % to 60 %: a lower bar shortens
PARTICIPATION halts because ACT-4 clears only at `activationThreshold` (`state.cpp:1243`). But the
threshold is what keeps a coalition that cannot hold the floors from switching enforcement on.
Measured on the real landscape (64 paths × 2 years, 7 bootstrap variants, `grid2`): at 70 %
ninjaraider + mining-dutch (65.7 %) locks in within 30 days with probability 0.34–0.97 and then
halts minting 1,700–4,500 h/yr; at 75 % it locks in with 0.00–0.09 within 90 days. A hop is worse:
ninjaraider + zpool (55.6 % on average, zpool 0 % for 52 days then 28 % for 8) locks in within 30
days with 0.50–0.97 at W ≤ 3,456 (0.03–0.66 at 4,032) and then spends 7,000–8,000 h/yr in
ENFORCEMENT — ACT-2 is a single-window test, so a few days of an auto-switching pool's hash are
enough.
**Decision.** The activation family minimises `act.spurious_lock` (the largest lock-in probability
among the top-6 coalitions with a 40–60 % mean share) subject to the expected coalition locking in
within `activation_reach_days` and the halt budgets; without a pool log it keeps the false-halt
objective. `max_spurious_lock_prob` (default 1 = off) can make it a constraint; the real-data
policy leaves it off because no window inside the runbook bound (W ≤ 6,912, D-2) brings the hop
below ≈ 0.4: **design note G5-DN-HOP** — the defence is procedural (publish `startHeight` only after
the operators who will enforce have committed, and watch `yed_getactivation` for a lock-in carried
by a pool that does not run the module) or a rule change (lock in only after two consecutive
windows above the threshold, `state.cpp:1084`; a consensus change, not proposed here).
The shipped 75 % stays (owner-pinned L3 as a fraction; the evidence supports it).

## D-RD-ACT-5 (2026-10-04, activation) — the work valve under a sustained race attack; the 64-note cap

**Finding.** The valve rule was "a non-enforcing minority's trip probability per incident ≤ 0.01"
(gambler's ruin). An incident is not rare: any owner of a matured vault can keep a sweep that does
not burn YED in every stock pool's mempool for free; each stock block mined on the enforcers' tip
starts a race, and a lost race returns the transaction to the mempool for the next one. On the
real block sequence (the non-enforcing operators' blocks, 30-day attack, 128 paths, 3 seeds and both
halves of the log; `valve2`): with the identified coalition enforcing (stock 28.1 %), P(trip within
30 days) is 1.00 at `valveBlocks` 6 (median 8 hours) and 8, 0.70–0.99 at 10–12, 0.35–0.62 at 16–24
and 0 at 32; with ninjaraider + flex (stock 23 %) 0 from 16; with everyone but the 3 % key 0.40–0.60
at 6 and 0 from 8 — a small miner's bursts (four blocks in a row) are enough at 6. The real sequence
is far burstier than iid blocks (iid at 28 % would give ≈ 0 at 16).
A long valve has its own failure: when enforcers are a genuine minority the stock branch grows
faster than its lead, and once a rejected root holds `VALVE_NOTE_CAP` = 64 notes (`index.h:65`,
`index.cpp:607`) the valve stops noting — the node stays split until an operator restarts it.
Exact (DP over lead × notes), at a stock share of 55 % (`detection_drop_share` 45 %): the share of
genuine-minority episodes that end stuck is 0 at 6–8, 1 % at 10, 4.3 % at 12, 21 % at 16, 68 % at 24.
**Decision.** New metrics `valve.attack_trip` (with `valve_attack_days`, real-data policy 30) and
`valve.capstuck` (constraint ≤ 0.05, `JUDGEMENT["valve_capstuck_max"]`). When no length meets the
attack bound the family reports the least-harm value — the smallest attack trip probability that
keeps the cap bound — with verdict BLOCKED: **12** on the real data (attack trip 0.80–0.84 in 30
days, median ≈ 8.5 days instead of 8 hours; cap-stuck 4.3 %). `valveBlocks` is owner-pinned (L7)
by the infra policy, so the report says KEEP 6 (owner decision) with this evidence; the risk of
keeping 6 is a valve trip within hours of a sustained attack.
**Design note G5-DN-VALVE.** No `valveBlocks` defends the identified coalition: the cure is coverage
(the flex 25 % enforcing: 16 suffices) or a node-local code change in a patch release — raise
`VALVE_NOTE_CAP` (≥ 256 makes 16–24 safe for a 55 % stock majority) and/or trip only when the
heavier branch's lead has persisted (a minority burst decays, a majority's lead grows).

## D-RD-ACT-6 (2026-10-04, activation) — adversarial scenarios from the real landscape

**Decision.** `scenarios/real-pools.toml` (7 scenarios, tag `activation`): ninjaraider offline for
1, 7 and 45 days (the rest renormalises to 41.3 %), ninjaraider signalling without enforcing, the
flex 25 % signalling without enforcing, a zpool hop, and a launch where only ninjaraider signals
and a 28 % pool joins for four days. `confirm_activation` runs every `activation` scenario (with
`hashrate-drop-*`) at the current and recommended sets: detection, recovery, W19 window,
abandonment, the blocks with enforcement on while the true enforcing share is below one half, and
the valve's trip probability under attack.
**What they show (shipped set; recommended in D-RD-ACT-2).** A one-day ninjaraider outage is not
detected by ENFORCEMENT (the window still averages above 50 %) but leaves enforcement on with a 41 %
minority for the whole day (1,152 blocks of ACT-7 exposure: any rule-breaking block trips every
enforcing node), and the PARTICIPATION halt it triggers holds until 75 % returns. Seven and 45 days:
ENFORCEMENT within ≈ 1,570 blocks (33 h), W19 opens one window later; 45 days abandons the module
after 30 (W21, by design). A rogue ninjaraider is never detected (the count stays 71.9 %): enforcement
stays on with 19.8 % enforcing for the whole scenario — the valve trips at once, which is the only
defence. The launch hop locks in within 3.4 days and then holds ENFORCEMENT until abandonment.

## D-RD-ACT-2 (2026-10-04, activation) — per-block replay; `signalWindow` 2,016 → 2,592 with the thresholds at their fractions

**Finding.** On the real landscape the shipped window gives the identified coalition (71.9 %)
43–91 false PARTICIPATION hours a year (0.2–0.9 episodes; budget 24 h, 2 flaps) in 6 of 7
bootstrap variants: the share swings by days (window sd 3.9 points), and an episode lasts until a
window reaches 75 % again (`state.cpp:1243`), p95 ≈ 12,000 blocks. ENFORCEMENT never sets (window
p01 62 % ≫ 50 %). The binomial mixture could not see this (block order), and the window family
mixed in rows that moved one threshold alone.
**Decision.** With a pool-share log G5 replays the real blocks (`ybcal.sim.landscape`); the window
family moves W only (`Family.requires`) on a grid centred on W0; a feasible new window resolves the
threshold families' halt-budget changes and every threshold keeps its fraction (75/60/50/60 %, the
L3 fractions). Result: **`signalWindow` 2,592** (2.25 d) with `activationThreshold` 1,944,
`participationFloor` 1,556, `enforcementFloor` 1,296, `enforcementResume` 1,556. At 2,592 the
seven variants give 0–22 false-halt h/yr and ≤ 0.16 flaps; detection p95 of a drop to 45 % 2,265
blocks (≤ 4,032); lock-in within 60 days 1.00 (median 5–8 days); freeze-then-fix runbook
2·2,592 + 16,128 + 4,608 = 25,920 ≤ `abandonBlocks` 34,560 (W21). 2,304 fails the false-halt budget
in some variants; ≥ 3,024 fails the 60-day reach in some (half 2: 0.97).
**Trade-off.** A longer window halts minting later on a real drop (≈ +500 blocks, 10 h) and
lengthens the W19 freeze window by 0.5 day; in exchange the honest-variance halts that would stop
minting for days several times a year disappear. With the flex 25 % enforcing every window is fine
(the shipped 2,016 included), so the change matters exactly in the expected landscape.

## D-RD-ACT-3 (2026-10-04, activation) — activation reach: 60 days, not the first window

**Finding.** `activation_reliability` 0.99 "in the first eligible window" at the policy share
0.70 against a 75 % threshold is false by construction (P = 9·10⁻⁴): the expected coalition's mean
is below the bar and it locks in on excursions (18 % of windows). That is an environment fact.
Frontier for the identified coalition (P(lock-in) within 30 / 60 days, 7 variants):
W 2,016 0.98–1.00 / 1.00; 2,592 0.84–1.00 / 1.00; 3,024 0.84–0.97 / 0.97–1.00; 4,032
0.59–0.94 / 0.81–1.00. Only 2,016 reaches 0.99 in 30 days, and it fails the false-halt budget by
2–4×.
**Decision.** New key `activation_reach_days` (default 0 = the old rule); the real-data policy sets
60: one sixth of the 12-month sunset, and the owner's 30-day planning floor (W21) makes a
one-month launch window too tight to also carry the false-halt budget. The owner chooses the point
on the frontier: 30 days ⇒ keep 2,016 and accept ≈ 70 false-halt h/yr; 60 days ⇒ 2,592.

## D-RD-ACT-7 (2026-10-04, activation) — what each adoption scenario gets (least harm)

| Coalition (mean share) | Activates? | Shipped set | Recommended (W 2,592) |
|---|---|---|---|
| all identified + flex (96.9 %) | first window | 0 false halts | 0 |
| ninjaraider + flex (77.1 %) | first window | ≈ 0–1 h/yr | 0 |
| identified (71.9 %, policy) | 30 d 0.98–1.00 | 0–91 h/yr (43–91 in 6 of 7) | 0–22 h/yr |
| identified + zpool hop (75.6 %) | 30 d 0.97–1.00 | 0–96 h/yr | 0–13 h/yr (47.5 on log half 1) |
| ninjaraider + mining-dutch (65.7 %) | no (≤ 9 % in 90 d) | must not; does not | does not |
| ninjaraider + zpool (55.6 %, hopping) | 0.50–0.97 in 30 d (hazard) | 7,000+ h ENFORCEMENT | same (G5-DN-HOP) |
| without ninjaraider (≤ 48 %) | impossible at any threshold ≥ 50 % | — | — |

Safety vs liveness: the 75 % bar (owner L3) is kept — lowering it to 70 % buys faster lock-in
(4 → 2 days) at the price of activating the 65.7 % coalition; the window is the knob that buys
liveness without lowering the bar.

## D-RD-ACT-9 (2026-10-04, activation) — the L3 pins are fractions

**Decision.** The integrator instructed the infra agent to pin `activationThreshold`,
`participationFloor`, `enforcementFloor` and `enforcementResume` (L3) as fractions of
`signalWindow` (75 / 60 / 50 / 60 %). G5 recommends their scaled absolute values only as a
consequence of the window change (each Recommendation's note says "resolved by signalWindow …, the
current fraction of the new window"); a pin on the absolute value would break the §1.4 ordering
at any new window. `valveBlocks` (L7) stays pinned: the report shows KEEP 6 with the evidence of
D-RD-ACT-5 (least harm 12) and the risk of keeping 6.

## D-RD-ACT-8 (2026-10-04, integrator) — devnet confirmation of signalWindow 2592 on both lines

**Decision.** `signalWindow` 2,592 (thresholds 1,944 / 1,556 / 1,296 / 1,556, the L3 fractions) is
confirmed on real nodes. Eight regtest runs (`ybcal devnet run --term-factor 1440`, real-landscape
step schedules from `landscape.devnet_steps`), shipped set vs the 2,592 set, ycash6 (pin 7702d22)
and ycash-dd (HEAD f78a5f8), all `devnet diff` PASS (892–1,017 heights × 13 fields):

| Schedule | Shipped (2,016) | 2,592 |
|---|---|---|
| real identified coalition (honest) | false PARTICIPATION halt, 246 blocks | no PARTICIPATION halt |
| ninjaraider offline 7 days | ENFORCEMENT halt, 246 blocks | ENFORCEMENT halt, 247 blocks |

Both node lines produced identical halt histories. The longer window removes the false halt
under the real landscape and still detects the loss of the majority pool.

**Robustness.** G5 standard: 2,592 at seeds 1 and 2; seed 3 picked 4,032, the first half of the
pool log 3,744, the second half kept 2,016. `ybcal robust` consolidation: 2,592 is feasible in 3/5
runs (s3 and h1 fail `false_halt`); the 7-variant landscape replay finds 2,592 the only window
meeting the 24 h/yr false-halt budget and 60-day lock-in in every variant. 2,592 stands, with the
residual: in the worst variants false halts reach ~22 h/yr.

## D-RD-COL-1 (2026-10-04, collateral) — long-horizon members come from the daily history

**Finding.** Volatility of real YEC depends strongly on the horizon (`yec-daily.csv`, 2019-07-20 →
2026-10-04; annualised σ of k-day log returns): 1 d 235 %, 7 d 166 %, 30 d 179 %, 90 d 160 %,
365 d 115 % (hourly file: 1 h 440 %, 1 d 228 %, 1 w 162 %, 1 y 108 %). A model's P(bad debt) over
60 days – 5 years is set by its dispersion at *those* horizons, not by its one-step σ. The hourly
file starts 2020-03-26, so a 5-year class-C term had ≈ 1.5 years of real start dates, and the
2019 fall was missing.
**Decision.** New module `ybcal.studies.g3_horizon`: members fitted on the daily series and
simulated hourly (Brownian bridge inside each day) through the same hour-mode oracle kernel —
`bootstrap-30d` / `bootstrap-365d` (stationary block bootstrap of daily log returns, demeaned,
D-RD-D1/AUD-1), `regime` (daily fit, centred), `martingale` (30-day bootstrap at zero arithmetic
drift, the stress convention), `history` (the real daily path, every start date). The frontier is
computed from sorted samples of x = true(claim opening)/pMint (exactly `p_bad_debt_fast`'s test,
tested to agree within 0.2 pp), so every locked ratio costs nothing. G3's real-data ensemble gains
`daily-bootstrap` (30-day blocks) and `daily-history` when `price_daily` is loaded (infra's
D-RD-INF-1 role; `long_daily(env)` also takes a `Daily`). Daily members are sized at the σ
multiplier quantile of the *hourly* history (`member_sigma`): a bridge's white intraday noise would
read as a 200 %+ σ̂ and a multiplier the market does not show. The claimant study keeps the hourly
members (hour-scale pClaim lag).
**Consequence.** `python -m ybcal.studies.g3_horizon frontier|study|claims` (see the module);
evidence `docs/evidence/collateral-2026-10/`.

## D-RD-COL-2 (2026-10-04, collateral) — the fork-launch fall is a sensitivity, not the base window

**Decision.** Daily members of the G3 ensemble fit the window from 2020-01-01 (`long_window_start`,
JUDGEMENT): YEC forked from Zcash in July 2019 and fell from $4.40 to $0.16 by January 2020 while
every ZEC holder's airdrop was sold — a one-off distribution event, not a market Yellowback will
mint into. The full window (2019-07 →) is reported beside it in every frontier table.
**Consequence.** Class A at the full window needs ≈ 975 % locked (bootstrap-30d) / 1,200 % (real
history) instead of 625 % / 475 %: the launch window is the single largest lever on A. The 2021–22
bear market (−90 %) and the 2025–26 cycle ($0.019 → $0.45 → $0.10 → $0.39) stay in.

## D-RD-COL-3 (2026-10-04, collateral) — a member must reproduce volatility by horizon

**Finding.** σ by horizon, ex-launch window (data vs seed-1 simulation, annualised, 1/7/30/90/365 d):
data 2.38/1.65/1.72/1.47/1.06; bootstrap-30d 2.25/1.66/1.65/1.58/1.50; bootstrap-365d
2.26/1.65/1.72/1.48/1.12; **regime 2.25/2.25/2.25/2.23/2.21** (no multi-day mean reversion: +36 %
at a week, +110 % at a year); martingale reads low because paths pin to `PRICE_MIN` (5-year bleed).
**Decision.** The daily regime switch is not a decision member (it fails the 7-day..1-year check on
every window); it and the martingale stay frontier stress rows. bootstrap-30d (conservative at a
year, +40 %) is the daily decision member; bootstrap-365d (matches every horizon, but resamples only
≈ 6 distinct years) is reported as the optimistic bound.

## D-RD-COL-4 (2026-10-04, collateral) — class tolerances B/C are an environment limit; the term classes as shipped

**Evidence** (standard, seeds 1–3, frontier in *locked* ratio = base × σ multiplier; P(bad debt at
claim opening) / mean shortfall, % of debt; tables in `frontier.csv`, per-term need in
`needed_by_term.csv`):

| class | locked ratio meeting tolerance (ex-launch: bootstrap-30d / bootstrap-365d / real history; hourly history) | full window (bootstrap-30d / history) | last 365 d (bootstrap-30d) | regime / martingale (stress) |
|---|---|---|---|---|
| A (0.5 %) | 625 % / 500 % / 475 %; 450 % | 975 % / 1,200 % | 1,600 % | 2,500 % / 1,100 % |
| B (1 %) | 1,800 % / 850 % / 675 %; 675 % | 3,000 % / 2,500 % | > 10,000 % | 10,000 % / none ≤ 10,000 % |
| C (2 %) | none ≤ 10,000 % / 4,000 % / 1,100 %; 1,100 % | none / 3,000 % | none | none / none |

Seeds agree within one lattice step (A ex-launch bootstrap-30d 625/675/600 %, B 1,800 % ×3).
Per term (ex-launch bootstrap-30d): A needs 450 % at 32 d → 775 % at 84 d; B 700 % at 99 d → 3,000 %
at 339 d; C 3,000 % at 410 d and > 10,000 % beyond ≈ 1.6 years. At the registry bounds (B 700 %,
C 600 % base at multiplier 1.0) the decision ensemble gives P(bad) B 12.6–13.6 % (ES 5.1–5.7 %),
C 32.9–35.7 % (ES 22–24 %); the real hourly history gives B 0.8 %, C 8.6 %. Real-history replay
(every real start date, ex-launch): every 30-day to 4-year start is covered by 1,800 % locked; the
worst one-year start (2021-12-04) needed 1,122 %; but n_eff = span / (term + grace) is 6.2 for 1-year
and 1.3 for 5-year terms — history cannot certify long terms. Node rules: `ycash6
src/yellowback/script.cpp:79-93` (owner path after `lockHeight`, claim path only after
`lockHeight + grace`), `state.cpp:515-517` (RED-2: every spend burns the whole debt),
`state.cpp:322-334` (MINT-5 sizes collateral at `MinRatioBps(base, σ)` and pMint), `math.h:105`.
**Decision.** `bad_debt_{B,C}` (and A's, should it ever be) are declared environment limits
(D-RD-INF-3) with design note G3-DN1: no ratio within the registry bounds meets them at YEC
volatility because nothing can liquidate a vault before `lockHeight + grace`. Least harm = the lowest
P(bad debt) = the registry upper bound (B 70,000, C 60,000), verdict CHANGE "policy unmeetable in
this environment" with the exposure, not BLOCKED.
**Owner-level design note (not a parameter change; D-R-6 pins the class bounds).** Class B (90–365 d)
and class C (1–5 y) as shipped are not viable for YEC under the policy tolerances: B's shortest
terms meet 1 % at ≈ 700 % locked, its longest need ≈ 3,000 %; C cannot meet 2 % at any ratio up to
10,000 % in any model that reproduces the data's volatility, except the real history itself
(1,100 %, n_eff 1.3–6). Levers, in order of effect: (1) shorten classMax[1]/[2] (B to ≈ 120 d at
700 %), (2) a rule that can act before maturity (periodic re-margining or a liquidation path above
θ), (3) relax `max_bad_debt_prob` for B/C with the frontier as the price list. Capital efficiency
falls as 1/R: 0.20 YED per USD locked at 500 %, 0.14 at 700 %, 0.05 at 2,000 %.

## D-RD-COL-5 (2026-10-04, collateral) — a seller meets the bid side of the book

**Decision.** `depth_p10_usd` reads `bid_depth_2pct_usd` (else half the two-sided figure): a
claimant selling YEC never touches the asks. On the 2026-10-04 books bids are $119 of $162 at p10.
`claim_stats` takes the vault's debt (`cents`) and `sell` (False = a holder who keeps the YEC).

## D-RD-COL-6 (2026-10-04, collateral) — the claimant is a YEC holder; nine claims in ten must pay

**Evidence** (standard, seeds 1–3, hourly members, worst member; `claim_liquidity_s*.csv`):
selling into the bid book, a $100 vault claims profitably at θ ≥ 115 %, a $1,000 vault needs
θ ≈ 145–150 % (mean −1,653 bps at 110 %, show-up 0.2 %), a $10,000 vault cannot be sold at all
(slippage capped at 99.99 %); YEC's p10 volume is ≈ $700/day. A holder who keeps the collateral
(no sale): θ 110 % mean +169..+196 bps, p10 −889..−915, show-up 58–59 %, already bad at the trigger
30 %; θ 122.5 % p10 +119..+146; **θ 125 % p10 +326..+353, mean +1,559..+1,588, show-up 91.4–91.7 %,
bad at trigger 6.1–6.3 %, absent-owner forfeit 1,696 bps** — identical across seeds and vault sizes.
**Decision.** Policy-free JUDGEMENT keys `claimant_model = "hold"`, `claim_margin_stat = "p10_bps"`:
the claimant that exists at YEC's liquidity is a holder, and a threshold where only the *mean*
claim pays leaves ~40 % of triggered vaults unclaimed under the pClaim lag (`pClaim =
max(pMid, pSlow)`, `state.cpp:540-551`). The seller is reported (`claim_sell.*`). Result:
`claimThresholdBps` 11,000 → **12,500** on every seed and both σ sets.
**Residual.** The holder needs YED to burn (RED-2) — YED market depth is unknown; and RED-5
(`state.cpp:559-561`) gives a RED-4(a) claimant everything up to θ × debt, so an owner absent after
`lockHeight + grace` forfeits up to 25 % of the debt (G3-DN2).

## D-RD-COL-7 (2026-10-04, collateral) — emergencyRatioBps: KEEP, and on real crashes it does not matter

**Evidence.** New scenario from the real history: the three worst non-overlapping 1-, 7- and 30-day
falls of the hourly file (−68 % 2021-06-18, −63 % one-hour wick 2022-01-25, −63 % 2025-10-10/11;
−71 % / −70 % / −66 % weeks; −79 % / −77 % months), vaults matured, ARMED. At θ 110–150 % the debt
left uncovered at closure is 0 at every `emergencyRatioBps` 101–149 %: RED-4(a) closes them first.
A RED-4(b) exit at par loses 1,000–4,500 bps of the debt (claimant paid at pClaim, the higher
price, `state.cpp:559-561`, R1) and only pays under a ≥ 10–45 % YED discount (G3-DN4). The synthetic
crash rows keep the PROVISIONAL KEEP 10,500.
**Decision.** KEEP 10,500; the rule-level fix of G3-DN4 (pay (b) at pEmerg or with a bounty) stands.

## D-RD-COL-8 (2026-10-04, collateral) — abandonBlocks: the "90 d" was an artefact; W21 30 d holds

**Finding.** The rd2 run's CHANGE 30 → 90 d came from (1) G5's share samples taking the top-2 payout
keys (ninjaraider 52 % + an unidentified 21 % key whose share is intermittent) instead of the
identified coalition, 11 % of them below `enforcementResume` (60 %), where an ENFORCEMENT episode
never ends, so P(false abandonment) = 1/yr at *every* value; and (2) G4's loop then stopped at the
registry ceiling (103,680) and reported a CHANGE that still violated. Real replay of the 61-day pool
log (70,000 blocks) with the identified coalition (ninjaraider + mining-dutch + dapool, D-RD-D5):
window share min 60.3 %, p5 65.9 %, **zero ENFORCEMENT episodes**; analytically P(false abandonment)
at a 60 % or 70 % share is < 1e-19/yr at 30 d. Without ninjaraider the share is 18.8 % (max 33 %):
ENFORCEMENT sets within a window and abandonment follows after `abandonBlocks` — a true loss of
enforcement, which abandonment exists to detect (`index.cpp:737`).
**Decision.** G4: when no value up to the bound meets false-abandon, verdict BLOCKED-keep with the
share of samples at/below resume (not a CHANGE that still violates). With D-RD-ACT-1's named
coalition the false-abandon axis is met at 30 d. W21 (owner floor, D-R-12) stands; the evidence
that argues for longer is only G4-DN1 (developers back after > 12 days cannot finish the M14 runbook
before owners may sweep; 90 d would tolerate 72 days, at the cost of 90 days of unsweepable vaults
if the developers never return) — shown, not a CHANGE.

## D-RD-COL-9 (2026-10-04, collateral) — the W20/W16 gate and the ratios

**Finding.** `MINT-6` and the HALT-2 gate (`state.cpp:318`, `:335-340`) admit a class iff
`MinRatioBps(base, σmult) ≥ recapRatioBps` (50,000). The owner's reading is "only class A mints
through a halt / above the cap". On the real hourly history the σ multiplier at the shipped
`sigmaRefBps` 10,000 has median 1.79 (p10 1.0, p90 3.0, capped 28 % of hours): A 500 %, B 400 %,
C 300 % lock 895 / 716 / 537 % at the median, so **every class already passes the gate** most of the
time. At `sigmaRefBps` 19,000 (G2, rd2) the median multiplier is 1.0: shipped B/C fail the gate,
but the least-harm values B 700 % / C 600 % pass it — the gate would then admit the riskiest
class (C: P(bad) ≈ 33 %) above the cap, the opposite of W20's "the marginal position is the safest".
**Decision.** No change here (supplyCapBps and the gate are G7's and owner-pinned W20). Recorded for
the owner: with base ratios set by drawdown, a ratio gate no longer ranks risk; a class-based gate
(class A only) or `recapRatioBps` ≥ the largest B/C locked ratio would restore the intent.

## D-RD-ORA-1 (2026-10-04, oracle) — the oracle quotes on the real pool landscape

**Finding.** With a pool-share log loaded, G1's oracle made every key ≥ 1 % tag (99.8 % of blocks)
while its analytic attack share used the policy's 0.70 tagging share; G2's ensembles ignored the
log entirely; and every miner was drawn i.i.d. from the 60-day average shares, so the real daily
swings (the 52 % pool at 39–62 % per day, zpool 0–29 %) never reached the fills.
**Decision.** `g1_price_windows.pool_landscape` reads the log (keys ≥ 1 %, largest 8); the tagging
set is the policy's `enforcing_pools` (shared with G5, D-RD-ACT-1: the operators that run the
overlay are the ones that quote; else the largest keys until `expected_enforcing_share`);
`realise` and G2's `realise_ensemble` replay the **real miner sequence** from a random offset per
path (`oracle.generate_block_inputs(miner=…)`), feed outages on top; the analytic attack share uses
the simulated tagging share. New metrics: the top pool's quote share and control probability per
window, NO_PRICE from the real sequence for the policy set, the adoption ladder (2/3/4 largest keys)
and the top pool withholding.
**Consequence.** Background NO_PRICE at the shipped windows is ~1,500–1,700 h/yr with the three
identified pools tagging (exact on the sequence alone: 1,526 h/yr), 232 h/yr with the four
largest, ~0 with every key; no window set reaches the 6 h/yr budget (docs/studies/g1.md).

## D-RD-ORA-2 (2026-10-04, oracle) — G1 is environment-limited; least harm is pMid 1,152

**Decision.** `environment_adjust` + `envlimit.attach_environment` (D-RD-INF-3): when one real pool
sets every median alone (`attack_env_blocked`) `attack_share_min` leaves the window decision
(G1-ENV-1), and when no set meets `max_no_price_hours` that constraint becomes "within 5 % of the
lowest NO_PRICE any set reaches" (G1-ENV-2; 5 %, not materiality: 20 % of 1,200 h/yr is 240 h of
minting downtime); J then picks inside the band with the minimal-change rule. Verdict KEEP/CHANGE
"policy unmeetable in this environment" with the exposure at the chosen set.
**Consequence.** pFastWindow 96 and pSlowWindow 2,016 stay; pMidWindow 576 → 1,152 (the registry
bound). It lowers NO_PRICE in every adoption case (3 identified pools −29 %, 4 largest −79 %) and
doubles the time a majority pool needs to capture pMid (8.6 → 17 h); pClaim's crash lag is set by
pSlow and does not move. The fix lies outside the windows: more tagging pools, or a mid/slow fill
below ⌈2W/3⌉ (at 0.6 W the identified-pool NO_PRICE falls from 1,526 to 144 h/yr; L9's guard
against a minority quote majority is covered by PRICE-2 once the attestation layer is ARMED) —
reported for the owner, not tuned.

## D-RD-ORA-3 (2026-10-04, oracle) — real-history replay is evidence on every G2 / G7 candidate

**Decision.** `studies/oracle_replay.py`: the real hourly price in order, interpolated onto blocks
(log-linear; `intra_hour="bridge"` brackets the intra-hour feed noise), quoted by the study oracle
(real landscape and sequence), through PRICE-1, SIGMA-1 and HALT-3 with the node's arithmetic.
Era metrics (full, last365, 2021-22, 2023, 2025-26) slice one replay; G2 attaches `replay_*` σ̂ and
multiplier figures (also with the one-hour prints removed), G7 `div.replay_*` HALT-3 / NO_PRICE /
real-fall recall / false-halt hours. Real falls: ≥ 30 % in a day or ≥ 50 % in a week on 6-hour
medians, caught when HALT-3 fires between the peak and a day after the trough; falls whose window
is mostly NO_PRICE cannot test HALT-3 and are left out.

## D-RD-ORA-4 (2026-10-04, oracle) — real-history and rogue-pool scenarios

**Decision.** Scenario base `model = "replay"` (`start`, or `select` worst_drawdown | best_rally |
max_vol with `window_days` / `rank`); G1 keeps a replay base instead of bootstrapping. New:
replay-worst-30d-{1,2,3}, replay-worst-90d, replay-cycle-2025-26-{rally,fall,spring},
replay-wick-2025-12-28, rogue-pool-52-{up,grief,withhold,offline}. G1's `rogue_metrics` turns the
largest real pool rogue on the real miner sequence (±10 %, −30 %, withholding).
**Finding.** No window stops the 52 % pool (72 % of the quotes): it moves pFast in ~1.4 h, pMid in
8.6 h (576) / 17 h (1,152), pSlow in ~35 h (2,016), then pMint is ≥ 5 % off on ~93 % of the attack's
blocks (pClaim on ~92 %). HALT-3 fires only while the medians disagree (~4 % of a −30 % attack).
Withholding gives NO_PRICE within ~0.6 h (no mints and, RED-4 needing pClaim, no claims) and ~38 h
of refill after it resumes. Once ARMED, PRICE-2 (state.cpp:366-379, math.h:286-296) bounds
over-minting and path-(a) claims; the residual is path (b) (pEmerg = min) and griefing.

## D-RD-ORA-5 (2026-10-04, oracle) — G2 on real data: daily regime fit; cap need on the real history

**Finding.** The calm/turbulent ensembles came from a regime fit to hourly returns (34 % / 773 %:
the aggregator noise read as a regime). The cap need rested on that turbulent Merton process.
**Decision.** Fit on daily observations (`daily_view`; ~80 % / ~350 %). With a real price the cap
need is the replayed pFast's σ̂ p99 over the whole history with the one-hour prints removed;
the ensemble's p99 is reported beside it. The reference keeps the M14 rule on the realised
bootstrap ensemble, cross-checked against the replay per era in the notes.
**Like for like.** The node's SIGMA-1 (state.cpp:1212-1221, math.h:72-104) is what the simulator
computes (`ybcal verify` 127/127; devnet). σ̂ on pFast (2-hour lower median, hourly samples, 42
returns, no demeaning) has a full-history median of 161–174 % against 440 % (1-hour returns) and
235 % (daily returns) for the raw series: the reference is compared with σ̂ on pFast only.

## D-RD-ORA-6 (2026-10-04, oracle) — HALT-3 availability read on the real history

**Finding.** `calm_availability` (D-RD-AUD-6) counted every HALT-3 hour in a bootstrap
`calm-90d` as false, but the bootstrap resamples the real crash days: 57 h/yr at 25 % against 2–6
h/yr of real false halts. And with ~17 % of blocks without a price under the real landscape, HALT-3
"missed" crashes during which HALT-1 already stopped minting.
**Decision.** With a real price the availability budget reads the worst era's false HALT-3 hours on
the replay (outside every ≥ 20 % weekly fall); recall (synthetic and real) is conditional on crash
windows where the medians are defined for most of the window (`div.untestable.*` reported).

## D-RD-ORA-7 (2026-10-04, oracle) — globalRatioHaltBps is owner-pinned in effect

**Finding.** P(the price falls to 1/halt within grace) at 250 %: 2.5 % on the real hourly series,
4.3 % on the daily series back to 2019, 6.4 % on the bootstrap, against a 1.6 % tolerance; ~300 %
meets it. §1.4 forbids a halt at or above class C's 300 % base ratio, and W16 (recap = 2 × halt,
owner decision D-R-3) would move the 500 % soft-cap gate W20 pinned (D-R-11) to 600 %.
**Decision.** `[owner_pinned] globalRatioHaltBps = "W16 (D-R-3) + W20 (D-R-11)"` in the real-data
policy: KEEP 25,000 with the exposure in a design note; `halt.p_sys_bad_real` added beside the
bootstrap figure.

## D-RD-ORA-8 (2026-10-04, oracle) — final values for G1, G2 and G7's halts, and how they were tested

| Parameter | Shipped | Final | Verdict | Evidence |
|---|---|---|---|---|
| `pFastWindow` | 96 | 96 | KEEP (env, G1-ENV-1/2) | quick robust 24/36 (last365 window and seed 20261004 pick 48) |
| `pMidWindow` | 576 | **1,152** | CHANGE (env) | quick robust 33/36 across windows × models × seeds (the rest 864); NO_PRICE frontier on the real sequence (docs/studies/g1.md) |
| `pSlowWindow` | 2,016 | 2,016 | KEEP (env) | 33/36 |
| `volWindow` / `volStep` | 2,016 / 48 | 2,016 / 48 | KEEP | standard seeds 1–3: KEEP / KEEP; quick robust 36/36 and 27/36 |
| `sigmaRefBps` | 10,000 | **18,000** | CHANGE | standard seeds 1–3: 17,500 / 18,000 / 18,500 (the median); real-history replay median σ̂ 161 % (log-linear) – 174 % (bridge); by window (quick, bootstrap): last365 15,000–23,000, 2021-22 24,500–29,500, 2025-26 17,500–19,500; regime model 10,000–19,000 (no hourly noise: not like for like). The shipped 10,000 is outside the M14 band in every run but 5/36 (regime) |
| `sigmaMultMaxBps` | 30,000 | **47,500** | CHANGE | standard seeds 1–3: 47,500 ×3 (need 4.52–4.71× = despiked full-history p99 σ̂ ≈ 830 % / ref); by window the need is 3× (2021-22) to > 5× (2025-26, CoinGecko's prints: 50,000-bound BLOCKED) |
| `divergenceBps` | 2,000 | **2,750** | CHANGE | standard seeds 1, 2: 2,750; seed 3: BLOCKED (2,750 meets availability 5.1 h/yr but synthetic recall 0.863 < 0.9; 2,500 fails availability 8.8 h/yr) — 2,750 is feasible in 2/3 and the least-violating in the third. Real-history frontier (both landscapes, pMid 576 and 1,152): worst-era false HALT-3 hours 2.3–5.0 at 2,750 vs 10.9–12.2 at 2,500; real-fall recall ~0.50 at 2,750, ~0.45 at 3,000. The quick grid (500-bps steps) cannot express 2,750 |
| `globalRatioHaltBps` | 25,000 | 25,000 | KEEP (pin W16 + W20) | P(system under water within grace) 2.5 % (real hourly) / 4.3 % (real daily) / 6.4 % (bootstrap) vs 1.6 %; ~300 % would meet it (D-RD-ORA-7) |
| `supplyCapBps` | 1,500 | 1,500 | KEEP (pin W20) | evidence only, unchanged by this work |

`emergencyPersist` is G8's (attestation), not G7's: not studied here.

**Devnet (both node lines).** The final set (mainnet-scale overlay pMidWindow 1,152,
sigmaRefBps 18,000, sigmaMultMaxBps 47,500, divergenceBps 2,750; scaled ×31.5, terms ×1,440) was
built for ycash6 at the pin (7702d22) and ycash-dd HEAD, and `ybcal devnet validate` passed all 8
scenarios on both (VALIDATED; 355–601 heights × 13 fields, vault rows, claimability, bundles): HALT-3
(DIVERGENCE) 31 blocks in crash-70, NO_PRICE through the feed outage (239 vs 143 warm-up blocks), the
σ multiplier at the 47,500 cap in K12 traps and 5 distinct values in the crash, HALT-2 296 blocks in
vault-cycle, identical on both lines. A run of the sharpest real one-day crash (CoinGecko, −51 % on
2025-10-10, 240 hourly prices) through `devnet run` + `devnet diff`: PASS on both lines (471 heights
× 13 fields), HALT-3 17 blocks, 39 distinct σ multipliers reaching the cap.

**Not done (handed over).** G1 at the standard budget (one seed ran > 1 h on the shared machine and
was stopped) and the standard robust matrix (36 runs): `.work/runs/robust-standard.sh` in this
worktree, resumable.
## D-RD-ATT-1 (2026-10-04, attestation) — honest pools and attestors quote what the shipped agents quote

**Decision.** G6's REG-4 streams and G8's MINT-10 / PIN models price an honest pool's quote and an
attestation as the shipped agents do: per venue a 15-minute window average, silence drop, a 10 %
outlier filter around the median, fail closed below `min_sources`/`min_venues`, then the median of the
kept venues (`ycash6 contrib/yellowback/yellowback_price.py` `PriceFeed.aggregate` lines 778-796;
`contrib/yellowback/attest/src/price.rs` `PriceFeed::aggregate`). `ybcal.sim.feeds.agent_quotes` is
parity-tested against the upstream Python agent (400 random cases, `tests/sim/test_feeds.py`). The
venues come from `VenueReplay`: each venue's real log deviation from the CoinGecko aggregate, read row
by row from the loaded spreads log at a random start per path. The WP-7d model — pool *i* reads venue
*i* mod 3 alone — is kept as policy `pool_feed = "venue"` and as the single-venue overlay
(`judge.venue_penalised.*`).
**Reason.** The one-venue model turned the venues' disagreement (worst real pair p95 1,906 bps) into
honest REG-4 deviation: honest p99 1,884 bps, calm p75 337 bps, 5.7 % false penalties, so
`deviationBps`, `peerLag` and `nPenalty` were BLOCKED in rd2. The agent median sits on the aggregate
(|agent − CoinGecko| p50 0–29 bps, p95 45–370 bps by year/window): honest p99 650–700 bps, calm p75
54 bps, calm false penalties ≈ 0.
**Consequence.** REG-4, PIN false pins and MINT-10 are now judged on the feeds the network will run.

## D-RD-ATT-2 (2026-10-04, attestation) — the sample agent configs must ship `min_sources = 2`

**Finding.** Both sample configs (`contrib/yellowback/pool/yellowback-quote.toml.sample:31`,
`attest/attest.toml.sample:35`) set `min_sources = 3` with exactly three venues and `max_age = 3600`
on SafeTrade and nonkyc. On the real venues (SafeTrade has no trade within an hour in 45 % of hours,
`spreads-reconstructed-maxage1h.csv`) every agent fails closed on **53 %** of blocks — synchronously, so
no pool writes a quote tag: FEE-0 on 22 % of mint heights and, through PRICE-1's fill rules, NO_PRICE
halts (G1). Without the `max_age` guard the outlier filter alone fails 8.6 % of blocks (FEE-0 3.2 %).
With `min_sources = 2` (`min_venues` 2 kept) the agent fails closed on 0.2–0.3 % of blocks.
**Decision.** `policy/real-data-2026-10.toml` models the agents at `agent_min_sources = 2` and the
report states the dependency: the agent sample configs must change (an agent-config change, no
consensus change; outside this repository — owner action). `agent_min_sources = 3` is run as a
sensitivity (`.work/robust-*` q-ms3).

## D-RD-ATT-3 (2026-10-04, attestation) — `divergeBpsAttest` is decided on pFast vs aMint, not on venue pairs

**Decision.** MINT-10 (`ycash6 src/yellowback/state.cpp:371-377`, W17) compares the pools' `pFast`
(PRICE-1 lower median of quote tags over 96 blocks) with the bundle's `aMint` (qLow weighted quantile
of m + k attestations). The rule is now: the smallest value, rounded up to 100 bps, at which MINT-10
refuses at most `max_mint10_refusal_prob` (1 %) of honest calm mints, both sides built by the shipped
agents on the real venue replay (`mint10_gaps`). The proposal's spreads.py rule (3 × the worst venue
pair's p95 → 5,000 bps on the reconstructed log) is reported as `div.target_spreads`.
**Reason.** Neither population reads one venue: the venue-pair spread measures a configuration no
agent ships. What separates pFast from aMint on real data is pFast's lag (a two-hour median) — gap p99
1,400–1,650 bps calm, 1,500–1,730 crash-70-1d, 1,600–1,870 pump-dump-3x. 5,000 bps would make MINT-10
inert; 1,500 refuses 0.9–1.3 % of honest calm mints. Theft is not at stake either way: pMint =
min(xMint, aMint), so a wide band only over-collateralises.
**Consequence.** `divergeBpsAttest` KEEP 1,500 (see D-RD-ATT-10 for windows and models).
An attestor set mis-configured on one venue raises calm refusals to 2.5–6 %.

## D-RD-ATT-4 (2026-10-04, attestation) — `bondMin` priced at the reference price; seat-splitting capture; security over one set lifetime

**Decision.** (1) Bug: the WP-7c code priced the bond at the *first* row of the loaded history
(2020-03, $0.075) and took the worst price of the whole 6.5-year history; it now uses the reference
price (the last real price, as G6) and one-year paths from it. rd2's "60,000 YEC" came from that bug.
(2) The cheapest capture is many seats of just over `bondMin`, not one heavy seat: with 9 seats and
m + k = 6, an adversary moves aMint up 10 % with P ≥ ½ from 7 seats (exact W9 selection + bundle
kernels, `seats_to_capture`) and down from 2. (3) `bondMin` is a verify family: security — the harmful
capture capital (7 × bondMin) covers `min_bond_cap_years` (1.0) of MINT-6 cap growth (supplyCapBps ×
subsidy since startHeight = 98,500 YEC/yr; both in YEC, so the test does not move with the price) —
and affordability — opportunity cost ≤ `attestor_min_monthly_revenue_usd` at the reference price.
**Reason.** A YEC-denominated bond's USD value is unknowable a year out (one-year p10–p90 of a
20,000 YEC bond: $730–$57,800 on the real bootstrap), but so is the cap's USD headroom: the ratio of
the two is price-free. One year is one parameter-set lifetime (`enforceUntilHeight` = start +
420,480): the successor set re-tunes the bond with observed attestor revenue.
**Frontier** (standard, $0.36): 15k YEC ratio 1.07, opp. $23/mo; **20k ratio 1.42, $30/mo**, harm
capital $50.5k, grief $21.7k; 25k 1.78, $38; 30k 2.13, $45; 35k 2.49, $53 (over the $50 floor).
Attestor revenue at feeBps 15 and low adoption is $40/seat/month: 20k is the largest round bond the
revenue covers. **KEEP 20,000 YEC.**
**Residual.** Harmful capture costs ≈ $50k of YEC bonds aged 180 days (`ageCap`) *plus* a colluding
pool majority (pMint = min) — and one real pool already mines 52 %. The attestors are the guard against
that pool; at YEC's market cap (~$5 M) $50k is affordable to a determined attacker. A renewal set
should raise the bond as adoption (and attestor revenue) grows.

## D-RD-ATT-5 (2026-10-04, attestation) — `attestInterval`: what the attestation age adds to MINT-10

**Decision.** The staleness constraint is now the MINT-10 refusal the attestations' *age* adds:
refusals with ages U{1..k} minus refusals with one-block-old attestations ≤ `max_mint10_refusal_prob`.
**Reason.** The closed form (2.326·σ·√maxAge at the hourly σ 440 %/yr) forced k 10 → 5 in rd2; a
direct "refusals at k ≤ 1 %" test then sat on a knife edge (1.15–1.25 % at standard) and preferred
*older* attestations (k 20–30), because pFast lags the market and older attestations are closer to it.
Both would have moved the locked `attestMaxAge` (= 2k, D-3). Neither is k's doing.
**Consequence.** `attestInterval` KEEP 10 (`attestMaxAge` 20 untouched). Node/agent cost of k = 5 had it
been chosen: 1.8 attestations relayed per block for 9 seats (budget 2.0) — cheap, but not needed.

## D-RD-ATT-6 (2026-10-04, attestation) — the real pool landscape as scenarios

**Decision.** New scenarios (`scenarios/rogue-major-pool.toml`, `major-pool-offline.toml`,
`venue-pool.toml`) driven in G6's judgement streams through the schedules `attacker_share` /
`attacker_bias_bps`, `offline_pool_share` and `venue_pool_share` (+ `constants.venue_pool_source`);
reported as `adv.*` metrics, never part of the honest p99.
**Findings (REG-4, `state.cpp:910-936`).** With the 52 % pool quoting +15 %, the peers' lower median is
its own quote in most windows: at `deviationBps` 1,000 the *honest* pools are penalised on ~40 % of
their tags and the liar on ~42 %; at 1,400 honest 0.04 %, liar 22 %; from ~1,550 neither (honest
deviation from the liar's median 1,304 bps, the liar's from the honest 1,500). +5 % is invisible to
REG-4 at any sane value. REG-4's penalty only steers the wallet's payee choice (`Penalized` →
`DefaultPayee`, `state.cpp:999-1011, 1029-1046`), so a majority liar costs honest pools fee income,
not consensus; the price protection against it is the attestors' min (PRICE-2) — design note.
The 52 % pool offline: peers stay sufficient at peerMin ≤ 12 only while the remaining pools tag; the
remaining enforcing share is then below `participationFloor`, so minting halts first.
A SafeTrade-only pool is penalised on 2–7 % of tags at 1,000–1,600 bps and is out of the accuracy band
most of the time: REG-4 notices it without the honest pools paying.

## D-RD-ATT-7 (2026-10-04, attestation) — `nPenalty` at the calm steady state

**Decision.** `pen.honest_excluded` (the share of time an honest pool is excluded from FEE-W) uses the
calm false-penalty rate; the stale-pools worst case is reported as `pen.honest_excluded_stress`
(16–18 % at 288). Below a tenth of `max_honest_exclusion` it counts as zero (noise).
**Reason.** rd2 used the stale-pools scenario (30 % of the hash an hour late for 85 days) as the steady
state; with the agent feeds the calm rate is ≈ 0 and relative "improvements" of 0.16 % → 0.11 % of the
time moved the wallet default.

## D-RD-ATT-8 (2026-10-04, attestation) — fees under the owner-pinned AFEE-1: the attestor floor is an environment limit

**Decision.** `attestFeeBps` stays 2,500 (D-3, owner-pinned): the fee grid is searched with it fixed;
the free optimum is evidence in the notes. At the policy adoption case no `feeBps` meets both the
minMint fee-share cap (2 %) and the $50/seat/month attestor floor (feeBps 15: share 1.69 %, attestor
$40; 20: 2.25 %, $54). The attestor floor is treated as environment-limited (D-RD-INF-3 shape,
note G6-ENV-1): keep the user-protecting cap, pay attestors the most it allows → **feeBps 25 → 15**.
**Frontier** (standard, low / mid / high adoption, per pool and per seat per month; round-trip share
of a minMint class-A vault, ARMED): 10 bps 1.12 %, pools $455 / $2,274 / $11,370, seats $27 / $135 /
$673; **15 bps 1.69 %, $681 / $3,406 / $17,032, $40 / $202 / $1,008**; 20 bps 2.25 %, $908 / $4,539 /
$22,697, $54 / $269 / $1,343; 25 bps 2.81 %, $1,135 / $5,673 / $28,364, $67 / $336 / $1,678.
**Owner choice.** The floor ($50) is a placeholder; the economic bound is the bond's opportunity
cost ($30/month at 20,000 YEC and $0.36), which 15 bps clears. If the owner prefers the floor over the
cap, 20 bps meets it at a 2.25 % round trip. Evidence on D-3: with AFEE free the grid's best point
does not need more than 25 % at 15 bps — the binding quantity is adoption, not the share.

## D-RD-ATT-9 (2026-10-04, attestation) — attestor capture priced in money

**Decision.** `scenarios/attestor-capture-capital.toml` (capital levels $10k–$500k at the reference
price, best split over ≤ 6 seats ≥ bondMin, nine honest minimum seats); G8 reports `capcap.<usd>.harm`
/ `.grief` at the current set.
**Finding** (20,000 YEC, $0.36): $25k → griefing in 95 % of bundles, no theft; **$50k → aMint up 10 %
in 80 % of bundles** (6–7 seats); ≥ $100k → every bundle. Theft additionally needs xMint pushed up.

## D-RD-ATT-10 (2026-10-04, attestation) — robustness runs and the final G6/G8 values

**Runs** (`.work/runs/robust.sh`, `ybcal robust --groups G6,G8 --max-rounds 1`, agents at
`min_sources = 2`): A standard × 3 seeds, full history, bootstrap, `spreads-reconstructed-maxage1h.csv`
(`.work/robust-std`); B quick × windows {full, last365, 2021-22, 2025-26} × models {bootstrap, regime,
martingale} (`.work/robust-wm`, 12 runs); C quick × 2 seeds on the un-guarded reconstruction
(`.work/robust-nomaxage`); D quick × 2 seeds on the live `spreads.py` log as of 2026-10-04 09:34 UTC
(54 rows, `.work/robust-live`). `martingale` (policy `price_drift`) does not change G6/G8's demeaned
bootstrap: those runs equal bootstrap.
**Final values** (verdict, where they agree):

| Param | Current → final | Evidence |
|---|---|---|
| `peerMin` | 5 → **12** | 17/17 runs (largest value with P(not evaluated) ≤ 5 % at the expected share and at the participation floor) |
| `deviationBps` | 1,000 → **1,700** | consolidated value feasible in 12/12 (B) and 3/3 (A); modal 1,400–1,600, window-sensitive (2021-22 needs 1,700): the binding case is a pool quoting an hour late (≤ 1 % of its tags); 20 % liars caught ≥ 99.9 %, 10 % liars no longer |
| `accuracyBandBps` | 300 → **100** | rule target in 17/17 (honest calm p75 54–57 bps with the agent feeds); wallet weighting only |
| `payeeWindow` | 100 → **200** | standard: FEE-0 0.16–0.28 % at 100 vs 0.1 % budget (synchronised agent fail-closed and pool outages); 200 feasible 3/3; quick runs keep 100 |
| `feeMin` | 0.5 → **0.2 YEC** | 17/17; the minMint edge-redeem test at `worst_price_usd` — conditional on G3's `baseRatioBps[0]` (rd2's joint set kept 0.5) |
| `feeBps` | 25 → **15** | environment limit G6-ENV-1 (D-RD-ATT-8), 17/17 |
| `attestFeeBps` | 2,500 KEEP | owner-pinned D-3 |
| `nPenalty` | 288 KEEP | consolidated 3/3; 192 differs by 0.05 % of the time (excluded wallet default) |
| `nReg`, `peerLag`, `accuracyWindow`, `payeeTiltBps` | KEEP | 17/17 |
| `divergeBpsAttest` | 1,500 KEEP | standard target 1,600–1,700 (within materiality); windows: last365 / 2021-22 bootstrap 2,100 (honest calm refusals at 1,500: 2.1–2.2 %), regime 900–1,100 (0.1–0.2 %); hourly bootstrap overstates sub-hour noise; a refusal delays a mint, never mis-prices it |
| `dormancyMinBundles` | 20 → **15** | consolidated 3/3 (rule's 12 also feasible; 15 is the smaller change) |
| `attestInterval` | 10 KEEP | age adds no refusals (−0.4 to 0 pp) in 17/17 |
| `qLowBps` | 3,333 KEEP | 17/17 (D-RD-ATT-11) |
| `bondMin` | 20,000 YEC KEEP | 17/17 (D-RD-ATT-4) |
| all other G8 fields | KEEP | 17/17; `emergencyPersist` PROVISIONAL (synthetic scenarios) |

**Live vs reconstructed spreads (D-RD-D3 bias).** 54 live rows (4.4 h): pair p50 cg–st 188, cg–nk 86,
st–nk 282 bps vs the reconstruction's last 7 days 231 / 67 / 380; live worst-pair p95 383 bps vs 1,142
(7 d) / 1,906 (365 d) reconstructed. The candle-close reconstruction plausibly inflates the tail 3–5×,
but 4.4 quiet hours cannot measure a p95. None of the final values depends on venue-pair tails any
more: the agents' median sits on the aggregate (|agent − CoinGecko| = 0 on every live row).

## D-RD-ATT-11 (2026-10-04, attestation) — `qLowBps` stays 3,333: griefing resistance is not bought with cheaper theft

**Decision.** The qLow family's constraints are theft-side: a 25 % single entity cannot move aMint up,
and a seat-splitting adversary needs ≥ `min_harm_capture_seats_share` (0.75) of the seats to move aMint
up with P ≥ ½. The proposal §7.2 griefing rule (a 25 % entity spans qLow) is reported.
**Reason.** Exact kernels: at qLow 3,333 theft needs 7 of 9 seats (P 0.61; 6 seats 0.22); at **any
qLow above one third** (3,350 … 3,500) 6 seats steal with P 0.77. rd2's 3,500 removed the 25 % entity's
griefing (0.91 → 0) at the price of making theft one seat (≈ $7k of YEC) cheaper. No qLow meets both;
griefing only over-collateralises new mints.

## D-RD-ATT-12 (2026-10-04, attestation) — devnet `attestor-dormancy` scenario

**Decision.** `ybcal.devnet.scenarios` gains `attestor-dormancy`: seats arm, node 0 mints every k + 2
blocks, seat 0 is dark for `dormancyBlocks + 4·dormancyCheck` blocks. `attestor-outage-1` stops a seat
for a third of two slow windows — shorter than the scaled mainnet `dormancyBlocks` (512) — so DORMANT
was never reached at mainnet timing.
**Result.** The final G6/G8 set (`docs/attest-wave2/recommended-g6g8.json`: mainnet + peerMin 12,
deviationBps 1,700, accuracyBandBps 100, payeeWindow 200, feeMin 0.2 YEC, feeBps 15,
dormancyMinBundles 15), scaled ×31.5 with terms ×1,440, overlay-built at ycash6 `7702d22` and ycash-dd
`f78a5f8`: the full suite **VALIDATED on both lines, 9/9 scenarios** — calm 423, crash-70 426,
hashrate-drop 487, attestor-outage-1 411 (22 bundles), oracle-attack-34 395, feed-outage 450,
vault-cycle 601 (3 vaults, 990 claimable rows), pin 355 (14 / 16 bundles), attestor-dormancy 815 heights
(89 bundles) — node and simulator equal block by block. In attestor-dormancy seat 0 never signs again
after its outage (DORMANT is final until REV-1); in attestor-outage-1 it returns. Counts are scaled by
the tool's floors (`dormancyMinBundles` 15 → 2 at regtest scale), so the devnet confirms the rules'
timing and arithmetic at the scaled set, not the mainnet count itself. Reports:
`.work/devnet/validate3-{y6,dd}.json` in this worktree.

## D-RD-FIN-1 (2026-10-05, integrator) — the October 2026 real-data recommendation

**Decision.** The recommended set is `docs/reports/2026-10-real/recommended.json` (20 changes),
chosen by `docs/reports/2026-10-real/consolidate.py` from four `ybcal robust` sweeps on the frozen
snapshot `data/local/frozen-20261004`: 6 standard runs (full history and last 365 days × 3 seeds,
the decision), 6 quick runs over the 2021–22 and 2025–26 regimes and 8 quick runs under the regime
and martingale price models. Rule: the standard runs decide (consolidated value — feasible in the
most runs, the runs' median for rule parameters, the modal least-harm value for environment limits;
`robust` basis column); a quick run moves a value only when the standard choice fails one of the
parameter's own constraints in a regime or a validated model (so `deviationBps` 1,700 → 1,800 for
2021–22; the regime model is excluded for G3, D-RD-COL-3, so `claimThresholdBps` is 12,500 not
13,250). `sigmaRefBps` takes the full-history median 18,000 — the conservative end, since a lower
reference raises the σ multiplier — over the last 365 days' 23,500–24,500. Validated on regtest
devnets of both node lines (8/8 scenarios each). Not lock-ready: the live spread log and depth
series need more calendar time, and the owner-level findings in the report's §3 are open.

## D-HD-1 (2026-10-05, hardening H0-b) — the hardening policy and the empty-class convention

**Decision.** `policy/harden-2026-10.toml` encodes the evidence-based hardening plan's owner
decisions (workspace `docs/plans/yellowback-evidence-based-hardening-plan.md` §3, as reshaped by
`docs/plans/yellowback-upgrade-plan.md` §7) on top of the real-data policy. Two new mechanisms carry
it. (1) **The base set** (`base_set`, `base_values`, `paramset.policy_base`): every run starts from
the October 2026 recommendation (`docs/reports/2026-10-real/recommended.json`) with the H values
applied — class A only (H-5), `mintRequiresArmed` (H-1), `attestArmMin` 7 (H-2), `feeBps` 15 /
`attestFeeBps` 5,000 (H-4), `maxMint` $2,500 (H-12), halt 300 % / recap 600 % (H-11) — and owner pins
hold those values (`Pin.holds` compares with the base, not the shipped column). The report's
"current" column is the base; `params.cpp.patch` is still written against the shipped source.
(2) **The empty-class convention** (H-5): a class is disabled by an empty term range,
`classMin[i] > classMax[i]`, concretely `classMax[i] = classMin[i] − 1` (B: 103,681 > 103,680; C:
420,481 > 420,480). The node's MINT-2/MINT-3 already refuse every term against an empty range, so this
is a value, not a schema change. Invariants read "contiguous or empty": class A must be non-empty, the
non-empty classes must be contiguous in order; `base_gt_halt`, `class_locktime` and `fee_floor_mintable`
read the enabled classes only. The studies follow: G3 sizes no ratio and moves no boundary into a
disabled class (its ratio is KEPT with a note), G4's grace constraint, G6's fee share, G7's
`halt_below_floor` and system tolerance, G9's ratio minimum and the minter demand mix skip disabled
classes; `scale_to_regtest` keeps a disabled class disabled. **Not modelled** (retired by the upgrade
plan §7, and not implemented on the v3 line either): the H-6 valve (persistence, note cap, 12 blocks;
gate G-6), H-7 two-window lock-in, H-9.1 sunset stand-down, F-3. `valveBlocks` keeps its L7 pin and
`max_spurious_lock_prob` stays report-only. H-10 (class-A-only soft-cap gate) is moot with B/C off
and is not in the simulator.

## D-HD-2 (2026-10-05, hardening H0-b) — `mintRequiresArmed` is a proposed registry field

**Decision.** H-1 adds a field to `yellowback::Params` that the pin `7702d22` does not have. The
registry carries it as a *proposed* field (`ParamSpec.proposed = "H-1"`): both columns are the pin's
behaviour (`false`), so the shipped set, the golden replay and the devnet differential at the pin are
unchanged; `check_drift` tolerates its absence and compares it once the node defines it;
`Extracted.registry_values` fills it for sets read from source; `recommended.json` carries it in its
columns. The policy sets `true` through `base_values`. The simulator's `mint_verdict` gains the MINT-4
clause (`mint-halted-unarmed`), placed after the existing halt bits and before MINT-5 (the order the
node chunk H3-a chooses must be mirrored here; the plan does not fix it). **Studies without an
attestation model** (no ARMED height in the timeline) run the vault book as if the clause were met:
launch gate G-5 puts the layer ARMED before minting opens, and pricing at `xMint` alone instead of
`min(xMint, aMint)` is the conservative side of PRICE-2 for collateral sizing. Without that, every
mint of an unarmed study would be VOID and G3/G4/G7 would score an empty book.

## D-HD-3 (2026-10-05, hardening H0-b) — the "worse window decides" ratio lock rule (H-3)

**Decision.** With `ratio_lock_rule = "worse-window"`, `ybcal robust` locks each parameter of
`ratio_lock_params` (class A's `baseRatioBps[0]`) at the largest of the per-window needs, where a
window's need is the smallest candidate whose own rule's constraints (`bad_debt_A`: P(bad debt at
claim opening) ≤ 0.5 %) hold in **every** run of that window (all seeds and models). Undecidable —
reported, never guessed — when a window has no finished run or no candidate feasible in all its runs.
"Every run" rather than "most runs" is the strict reading of H-3's "meets class A's 0.5 % on both the
full-history and the last-365-day standard runs". Rendered in `robust.md` § "Worse-window lock rule"
and `robust.json` (`summary[].worse_window`).

## D-HD-4 (2026-10-05, hardening H4-a) — class A's ratio is searched past the registry bound

**Decision.** Policy key `search_bounds` replaces a study's search bounds for listed parameters (G3's
ratio lattice; `G3Study.with_policy`, called by the runner before `space`). The hardening policy
searches `baseRatioBps[0]` on [30,000, 250,000]: the first H4-a runs showed that on the last-365-day
window P(bad debt at claim opening) of class A is still ≈ 3.9 % at the registry's 80,000 bound, so the
worse-window rule (D-HD-3) would be undecidable inside the registry bounds. The registry bound is a
search bound, not a rule; a value above it is evaluated like any other and is still subject to every
invariant.

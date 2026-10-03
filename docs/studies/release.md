# Release study — `startHeight`, `enforceUntilHeight` (verified and derived, never tuned)

Code: `src/ybcal/studies/release.py` (WP-7b, group `R`). Plan: [PLAN.md §5.11](../PLAN.md).
Tests: `tests/studies/test_release.py`. Decisions: D-WP7b-6.

## Inputs

| Input | Source (first that is set) |
|---|---|
| release tip | `env.data["release_tip"]` (CLI / report driver) → `policy.release_tip` → **3,052,055**, the 6.21.0-rc1 mainnet tip in the comment above `MainParams()` (`src/yellowback/params.cpp` @ 7702d22) |
| tip date | `env.data["release_tip_date"]` → `policy.release_tip_date` → 2026-10-02 (same comment) |
| next network upgrade | `env.data["next_upgrade_height"]` → `policy.next_upgrade_height` → `git show 7702d22:src/chainparams.cpp`, `CMainParams`, the smallest `nActivationHeight` above the tip (none: NU5, NU6, NU6_1, NU6_2 and ZFUTURE are `NO_ACTIVATION_HEIGHT`; the parsed table is recorded as `PINNED_UPGRADES` for runs without a clone, and a test checks the two agree) |
| `release_lead_blocks`, `renewal_lead_blocks`, `runbook_operator_buffer_blocks` | policy (16,128 / 210,240 / 4,608) |

## Rules

- **startHeight**: KEEP while `startHeight ≥ tip + release_lead_blocks` (M14); otherwise the smallest
  multiple of 1,000 that clears it (D-WP7b-6: round numbers, as the shipped value).
- **enforceUntilHeight** = `startHeight + 420,480` (L8) of the recommended start, never past the next
  scheduled upgrade (then capped at it, with the shortened year noted).

Verdicts use provenance `judgement` with the default tip and `real-data` when a tip is supplied, so a
release recommendation is never PROVISIONAL. The release-relevant invariants (`release_lead`,
`sunset`, `start_configured`, `class_locktime`) are re-checked on the recommended set with the tip and
the upgrade in the context.

## Reported

| Metric | Definition |
|---|---|
| `lead_margin` | `start − tip − 16,128` (6,817 at the shipped values) |
| `latest_tip` | the last tip at which the release can be tagged for this start (3,058,872 ≈ 2026-10-07) |
| `renewal_deadline` | W18: `sunset − renewal_lead_blocks` (3,285,240 ≈ 2027-04-22) |
| `runbook` | `2·signalWindow + lead + buffer` (24,768) |
| `runbook_slack` | `sunset − renewal_deadline − runbook`: a defect found at the renewal deadline can still be fixed by freeze-then-fix before the sunset (185,472 blocks) |
| `abandon_margin` | `abandonBlocks − runbook` (9,792 at the shipped values; G4 recomputes it for its recommendation) |
| dates | at 75 s a block from the tip date: start ≈ 2026-10-21, sunset ≈ 2027-10-21 |

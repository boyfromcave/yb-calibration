# Example report — quick budget, synthetic data

Output of `ybcal recommend --budget quick --synthetic --workers 4` at ycash6 `7702d22`, run on
2026-10-03 (9 min 52 s on 4 cores). Kept as a reference for what the tool produces; **every value
rests on synthetic placeholder data and is not a recommendation to lock** (lock-ready: no).
The self-contained `report.html` and the `evidence/` figures are not committed (regenerate with
`make quick`).

| File | What |
|---|---|
| `report.md` | the full report (executive summary, one section per parameter, design notes, lock-readiness) |
| `recommended.json` | the recommended set, in `ybcal params extract` shape |
| `params.cpp.patch` | proposed locked/per-release changes to `src/yellowback/params.cpp` (never applied by the tool) |
| `params-patch-release.patch` | proposed excluded (patch-release) changes |
| `manifest.json` | everything needed to reproduce the run (`ybcal recommend --manifest`) |

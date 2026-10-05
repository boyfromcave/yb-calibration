"""Consolidate the 2026-10 real-data sweeps into one recommended parameter set (D-RD-FIN-1).

Inputs (all under .work/robust/, produced by `ybcal robust` on the frozen snapshot
data/local/frozen-20261004, see SHA256SUMS there):

- final-std            6 runs, budget standard, windows full + last365, demeaned bootstrap, 3 seeds
                       (the decision runs);
- final-quick-windows  6 runs, budget quick, the 2021-22 and 2025-26 regimes, 3 seeds;
- final-quick-stress   8 runs, budget quick, regime + martingale price models, 2 windows, 2 seeds;
- final-all            the tabulation of all 20.

Rule, per parameter: the standard runs decide; the quick runs may only move a value further in
the safe direction when the standard choice fails one of the parameter's own constraints in a
regime or a validated price model (the regime model is excluded for G3, D-RD-COL-3). Owner pins are
held. Each choice below cites the evidence; the report (README.md beside this file) explains it.

Run from the repo root:  .venv/bin/python docs/reports/2026-10-real/consolidate.py
"""

from __future__ import annotations

import json
from pathlib import Path

from ybcal.params import emit as E
from ybcal.params.invariants import Context
from ybcal.params.paramset import mainnet

OUT = Path(__file__).parent

# parameter -> (value, basis). Values not listed stay at the shipped set (KEEP or owner pin).
FINAL: dict[str, tuple[int, str]] = {
    # G1 price medians — environment-limited (52 % pool sets every median; NO_PRICE): least harm,
    # unanimous in all 18 standard runs (6 final + 12 of the crashed 2026-10-04 sweep)
    "pMidWindow": (1008, "least-harm, 6/6 standard (and 12/12 of the earlier standard sweep)"),
    "pSlowWindow": (1152, "least-harm, 6/6 standard (and 12/12 of the earlier standard sweep)"),
    # G2 volatility — rule parameters: median of the standard runs; full-history window, the
    # conservative end (a lower reference means a higher multiplier); oracle agent's in-order replay
    "sigmaRefBps": (18000, "median of 6 standard runs (17,000-24,500; full window 17,000-18,000)"),
    "sigmaMultMaxBps": (47500, "median of 20 runs; real-history p99 need 4.52-4.71x (D-RD-ORA)"),
    # G3 collateral
    "baseRatioBps[0]": (72500, "feasible in the most standard runs (3/6; last365 needs more)"),
    "baseRatioBps[1]": (62500, "least-harm (environment limit G3-DN1), modal of 6 standard"),
    "claimThresholdBps": (12500, "feasible in every run except 3 under the excluded regime model"),
    # G5 activation — L3 thresholds are pinned fractions (75/60/50/60 %) of signalWindow
    "signalWindow": (2592, "feasible 6/6 standard; fails false_halt only in quick seed 20261004; "
                           "devnet-confirmed on both lines (D-RD-ACT-8)"),
    "activationThreshold": (1944, "75 % of signalWindow (L3 pin)"),
    "participationFloor": (1556, "60 % of signalWindow (L3 pin)"),
    "enforcementFloor": (1296, "50 % of signalWindow (L3 pin)"),
    "enforcementResume": (1556, "60 % of signalWindow (L3 pin)"),
    # G6 judgement and fees
    "peerMin": (12, "median, 20/20 runs"),
    "deviationBps": (1800, "feasible 19/20 (1,700 fails false_penalty_stale in all three 2021-22 runs)"),
    "accuracyBandBps": (100, "least-harm, 20/20 runs"),
    "feeMin": (40000000, "feasible 20/20 (0.5 YEC fails in some runs)"),
    "feeBps": (10, "least-harm (environment limit G6-ENV-1), 17/20 runs"),
    # G7 halts
    "divergenceBps": (2500, "feasible in the most runs (7/20); 2,000 fails calm_availability in 20/20"),
    # G8 attestation
    "dormancyMinBundles": (15, "feasible 20/20, closest to current"),
    # G9 wallet policy (patch release)
    "walletConfirmations": (24, "20/20 runs; sized against the 21 % second pool (G9-ENV-2)"),
}


def main() -> int:
    base = mainnet()
    rec = base.replace({k: v for k, (v, _) in FINAL.items()})
    viol = rec.check(Context(release_tip=3052055))
    for v in viol:
        print("INVARIANT", v)
    E.write_recommended(OUT / "recommended.json", rec, base=base,
                        extra={"basis": {k: b for k, (_, b) in FINAL.items()}})
    patch = E.make_patch(rec.to_dict(), base.to_dict())
    (OUT / "params.cpp.patch").write_text(patch.locked)
    (OUT / "params-patch-release.patch").write_text(patch.patch_release)
    if patch.unpatched:
        print("UNPATCHED", patch.unpatched)
    rows = [f"| `{k}` | {base[k]:,} | **{rec[k]:,}** | {b} |" for k, (_, b) in FINAL.items()]
    (OUT / "changes.md").write_text(
        "| Parameter | Shipped | Recommended | Basis |\n|---|---|---|---|\n" + "\n".join(rows) + "\n")
    print(json.dumps({"changes": len(FINAL), "invariant_violations": len(viol), "digest": rec.digest()[:12]}))
    return 1 if viol else 0


if __name__ == "__main__":
    raise SystemExit(main())

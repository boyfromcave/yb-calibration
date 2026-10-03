# yb-calibration

Calibration tool for the Ycash Yellowback (YED) parameter set (`yellowback::Params` in
`boyfromcave/ycash6`, `src/yellowback/params.cpp`, branch `feature/yellowback`).

The tool (`ybcal`) will:
- read the parameter set from source and classify each field as locked, excluded or other;
- simulate the overlay with the node's exact integer arithmetic;
- stress each parameter group against real and synthetic YEC price histories;
- optionally check the results on a regtest devnet;
- write a report with a recommended value and an explanation for every parameter.

**Status:** planning. See [docs/PLAN.md](docs/PLAN.md) for the implementation plan.

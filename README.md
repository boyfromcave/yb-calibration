# yb-calibration

Calibration tool for the Ycash Yellowback (YED) parameter set (`yellowback::Params` in
`boyfromcave/ycash6`, `src/yellowback/params.cpp`, branch `feature/yellowback`).

The tool (`ybcal`) will:
- read the parameter set from source and classify each field as locked, excluded or other;
- simulate the overlay with the node's exact integer arithmetic;
- stress each parameter group against real and synthetic YEC price histories;
- optionally check the results on a regtest devnet;
- write a report with a recommended value and an explanation for every parameter.

**Status:** WP-0 (scaffold and contracts) landed: `ybcal params show | extract | check | doc` work;
other commands report which work package will implement them. See [docs/PLAN.md](docs/PLAN.md) for
the plan, [docs/architecture.md](docs/architecture.md) for the module map and contracts, and
[docs/parameters.md](docs/parameters.md) for every parameter.

```bash
make setup                                   # .venv + pip install -e '.[dev]'
make test lint
.venv/bin/ybcal params show --group G3
.venv/bin/ybcal params check --ycash6 ../ycash6   # drift vs registry + invariants (snapshot if omitted)
```

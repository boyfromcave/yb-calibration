"""Search, robust aggregation, Pareto selection, sensitivity (PLAN §5.10).

Owner: WP-6 (``joint.py``: WP-8). Modules:

* :mod:`~ybcal.optimize.evaluate` — per-candidate environments, the result cache, parallel scoring
* :mod:`~ybcal.optimize.search` — axes, grid, Latin hypercube, neighbourhood, successive halving
* :mod:`~ybcal.optimize.robust` — CVaR, quantiles, minimax regret, policy-constrained selection
* :mod:`~ybcal.optimize.pareto` — non-dominated sort, knee point, feasible-primary selection
* :mod:`~ybcal.optimize.sensitivity` — OAT, Morris, Sobol (Saltelli + Jansen), report sentences
* :mod:`~ybcal.optimize.runner` — ``optimize_group`` / ``run_group`` over the Study protocol
"""

OWNER_WP = "WP-6"

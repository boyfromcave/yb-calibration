# ybcal methodology

How the tool turns simulations into recommendations. WP-10 integrates the per-WP sections.

## Search, robustness and sensitivity

*Owner: WP-6 (`src/ybcal/optimize/`). Decisions: D-WP6-1 … D-WP6-7.*

**Candidate sets.** Each parameter is searched on a lattice of its registry step anchored on the
current value, inside the registry's hard bounds, so the current value is always a candidate and
"one step" is a fixed, documented quantity. A study either supplies its own candidate list
(`space()`), or the optimizer generates one: a full factorial grid (thinned to the budget's points
per axis, capped), a Latin hypercube (stratified on every axis, snapped to the lattice,
deduplicated), or a successive-halving run on either. Coupled parameters move together: derived
values are recomputed from their parents, and explicit couplings (e.g. adjacent class bounds) are
declared as rules. Every candidate is checked against the PLAN §1.4 invariants and any feasibility
rule before it is simulated; rejected candidates are counted by the invariant that rejected them and
reported.

**Fidelity.** Successive halving scores all candidates with few Monte-Carlo paths, keeps the best
third (always including the current set), and re-scores the survivors with three times the paths,
ending at the full budget. All candidates see the same random paths (common random numbers), so
differences between them are not sampling noise, and results do not depend on how many processes
evaluated them.

**Robustness.** Over a scenario ensemble a candidate is summarised by its mean, a quantile, its
CVaR (the mean of the worst `1 − α` probability mass) or its worst case. The default final rule is
**minimax regret**: in each scenario, regret is the gap to the best policy-feasible candidate in that
scenario; the chosen value minimises the largest regret. Policy tolerances are hard constraints on
an aggregate of a metric (e.g. CVaR of bad-debt probability ≤ the class limit). If no candidate meets
every constraint, the least-violating one is reported as **BLOCKED**. Ties go to the current value,
then to the smallest change.

**Trade-offs.** Where a study balances several objectives, the tool reports the Pareto front
(non-dominated candidates), its knee (the best balance after normalising each objective), and the
policy-feasible candidate that is best on the parameter's primary metric — the one recommended.

**Why not the neighbours.** After a study decides, each tunable parameter is moved one step up and
one step down from the recommended set (all else equal) and re-scored. The report shows those
scores; a neighbour that scores better says whether the gain is within materiality (so the
minimal-change rule kept the recommendation) or beyond it.

**Sensitivity.**

* *One at a time:* sweep a parameter, classify each segment by its elasticity — flat (|e| < 0.1),
  moderate, or steep (|e| ≥ 1) — and state it in a sentence such as "P(owner miss) is flat between
  20 and 40 days; P(miss) dominates below 20 days."
* *Morris screening:* random one-factor-at-a-time trajectories on the parameters' integer lattices;
  μ* (mean absolute effect) ranks influence, σ flags non-linearity or interaction.
* *Sobol indices:* first-order S1 (share of output variance explained by a parameter alone) and
  total-order ST (including interactions), estimated with Saltelli's design and Jansen's estimators
  on ±1 step around the joint recommended set, with bootstrap confidence intervals. A parameter
  whose ST is below `policy.insensitive_total_order` is labelled *insensitive*, and its current value
  is kept. The estimators are validated on the Ishigami function, whose indices are known exactly.

"""Reference model and exact kernels (owner: WP-1; PLAN §2.1).

- :mod:`.reference`, :mod:`.reference_attest`, :mod:`.reference_util` — the ycash6 pure-Python
  model, vendored at the pin (do not edit; ``ybcal verify --revendor``), managed by :mod:`.vendor`;
- :mod:`.kernels` — exact scalar integer kernels (math.h / state.cpp), ``None`` = undefined;
- :mod:`.vkernels` — numpy equivalents for Monte Carlo, ``-1`` = undefined;
- :mod:`.golden`, :mod:`.examples`, :mod:`.parity` — the checks ``ybcal verify`` (:mod:`.cli`) runs.
"""

OWNER_WP = "WP-1"

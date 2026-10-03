"""Parameter registry, extraction from ycash6 source, invariants and parameter sets (WP-0).

``scaling.py`` (mainnet → regtest time scaling) belongs to WP-9; ``emit.py`` (recommended set →
params.cpp patch) belongs to WP-8.
"""

from ybcal.params.registry import PINNED_COMMIT, REGISTRY, ParamSpec

__all__ = ["PINNED_COMMIT", "REGISTRY", "ParamSpec"]

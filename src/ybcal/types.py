"""Shared value types frozen in WP-0 (PLAN §3.1).

``PricePath`` lives here (not in ``ybcal.data``) so that the simulator, the data layer and the
studies can all import it without depending on each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import numpy as np

#: A parameter value: integers for every consensus quantity, ``bool`` for flags, ``str`` for enums,
#: hex byte strings and the network name.
ParamValue = int | bool | str

#: Provenance tag of a result (PLAN §2.5). ``judgement`` = an owner/analyst decision, not data.
Provenance = Literal["real-data", "synthetic", "judgement"]

#: Provenance of a price path.
PathProvenance = Literal["real", "synthetic", "scenario"]

#: Resolution of a price path: 75-second blocks or hours (48 blocks).
Resolution = Literal["block", "hour"]


@dataclass
class PricePath:
    """One or many price paths, block- or hour-indexed.

    ``prices`` has shape ``(paths, n)`` and dtype ``int64``, in micro-USD per YEC. A value of
    ``0`` means "no price" (feed gap) where a consumer supports gaps.
    """

    t0: datetime
    resolution: Resolution
    prices: np.ndarray
    provenance: PathProvenance
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        arr = np.asarray(self.prices)
        if arr.ndim == 1:
            arr = arr[np.newaxis, :]
        if arr.ndim != 2:
            raise ValueError("PricePath.prices must have shape (paths, n)")
        if arr.dtype != np.int64:
            if not np.issubdtype(arr.dtype, np.integer):
                raise TypeError("PricePath.prices must be integer micro-USD (int64)")
            arr = arr.astype(np.int64)
        self.prices = arr

    @property
    def n_paths(self) -> int:
        """Number of paths."""
        return int(self.prices.shape[0])

    @property
    def n_steps(self) -> int:
        """Number of steps per path."""
        return int(self.prices.shape[1])

    @property
    def step_blocks(self) -> int:
        """Blocks per step (1 for block resolution, 48 for hour resolution)."""
        return 1 if self.resolution == "block" else 48

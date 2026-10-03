"""Shared integer units and tiny exact helpers (owner: WP-0).

Every consensus quantity in ``ybcal`` is an integer: zat, US cents, micro-USD per YEC, basis
points, blocks. These mirror the constants of ``src/yellowback/params.h`` and ``math.h`` at the
pinned ycash6 commit; the registry (``ybcal.params.registry``) re-checks them against source.
"""

from __future__ import annotations

from collections.abc import Iterable

#: zat per YEC (``amount.h``).
COIN: int = 100_000_000
#: Basis points in 100 % (``math.h`` BPS).
BPS: int = 10_000
#: micro-USD in one US dollar (prices are integer µUSD per YEC).
MICRO_USD_PER_USD: int = 1_000_000
#: micro-USD in one US cent (YED amounts are integer cents).
MICRO_USD_PER_CENT: int = 10_000

#: Blocks per hour / day / year at the 75-second post-Blossom spacing (``params.h``).
BLOCK_SECONDS: int = 75
BLOCKS_PER_HOUR: int = 48
BLOCKS_PER_DAY: int = 1_152
BLOCKS_PER_YEAR: int = 420_480

#: Price bounds in micro-USD per YEC (``params.h`` PRICE_MIN / PRICE_MAX).
PRICE_MIN: int = 100
PRICE_MAX: int = 100_000_000

#: CLTV threshold: lock heights must stay below it to be read as heights, not times.
LOCKTIME_THRESHOLD: int = 500_000_000

#: Minimum lead between a release tip and a new set's start height (M14: two weeks of blocks).
RELEASE_LEAD_BLOCKS: int = 16_128


def ceil_div(a: int, b: int) -> int:
    """Exact ceiling of ``a / b`` for integers (``b > 0``); never touches floats."""
    if not isinstance(a, int) or not isinstance(b, int):
        raise TypeError("ceil_div takes integers")
    if b <= 0:
        raise ValueError("ceil_div divisor must be positive")
    return -((-a) // b)


def lower_median(values: Iterable[int]) -> int | None:
    """``math.h`` LowerMedian: element ``(n - 1) // 2`` of the ascending sort; ``None`` if empty.

    Integer-only: raises ``TypeError`` on any non-integer element (bools rejected too).
    """
    v = list(values)
    if not v:
        return None
    for x in v:
        if not isinstance(x, int) or isinstance(x, bool):
            raise TypeError("lower_median takes integers only")
    v.sort()
    return v[(len(v) - 1) // 2]


def blocks_to_days(blocks: int) -> float:
    """Blocks as days, for display only (never feed the result back into a rule)."""
    return blocks / BLOCKS_PER_DAY


def days_to_blocks(days: int) -> int:
    """Whole days as blocks."""
    return days * BLOCKS_PER_DAY

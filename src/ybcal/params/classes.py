"""Term classes and the empty-class convention (hardening plan H-5).

A class is **disabled** by giving it an empty term range, ``classMin[i] > classMax[i]``. The node
needs no new code path for it: MINT-2 / MINT-3 (``Params::ClassForLockBlocks`` and the class-term
check, ``state.cpp:303``) match no lock length against an empty range, so every term of that class
is refused with the existing ``mint-class-term`` verdict. An empty range is a value, not a schema
change: the golden vector's ``ParamsRecord`` keeps its shape (a ``classEnabled`` mask would not).

The convention used by the hardening policy keeps ``classMin[i]`` and sets
``classMax[i] = classMin[i] - 1`` (the smallest change that empties the range).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: NUM_CLASSES (params.h).
NUM_CLASSES: int = 3
CLASS_NAMES: tuple[str, ...] = ("A", "B", "C")


def class_range(params: Mapping[str, Any], i: int) -> tuple[int, int]:
    """``(classMin[i], classMax[i])``."""
    return int(params[f"classMin[{i}]"]), int(params[f"classMax[{i}]"])


def class_enabled(params: Mapping[str, Any], i: int) -> bool:
    """False iff class ``i`` has an empty term range (``classMin[i] > classMax[i]``)."""
    lo, hi = class_range(params, i)
    return lo <= hi


def enabled_classes(params: Mapping[str, Any]) -> tuple[int, ...]:
    """Indices of the classes a mint can use, in order."""
    return tuple(i for i in range(NUM_CLASSES) if class_enabled(params, i))


def disabled_classes(params: Mapping[str, Any]) -> tuple[int, ...]:
    """Indices of the classes with an empty term range."""
    return tuple(i for i in range(NUM_CLASSES) if not class_enabled(params, i))


def disable(i: int, params: Mapping[str, Any]) -> dict[str, int]:
    """The change that empties class ``i`` under the convention (``classMax = classMin - 1``)."""
    return {f"classMax[{i}]": int(params[f"classMin[{i}]"]) - 1}

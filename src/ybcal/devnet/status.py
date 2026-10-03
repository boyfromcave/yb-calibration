"""Outcome types shared by the devnet layer: a step either ran or was *skipped* with a reason.

Owner: WP-9.

PLAN §6.2: where the environment cannot build or run nodes the devnet layer is reported as
**skipped**, never faked. Every live entry point returns :class:`Skipped` instead of raising
when the cause is the environment (no binary, no toolchain, blocked network), and raises when the
cause is a bug or a refused request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeGuard

OWNER_WP = "WP-9"


@dataclass(frozen=True)
class Skipped:
    """A step that did not run, and why (printed as ``skipped: <reason>``)."""

    reason: str
    step: str = ""

    def __str__(self) -> str:
        return f"skipped{' (' + self.step + ')' if self.step else ''}: {self.reason}"

    def to_dict(self) -> dict[str, str]:
        """JSON form."""
        return {"status": "skipped", "step": self.step, "reason": self.reason}


def is_skipped(x: Any) -> TypeGuard[Skipped]:
    """True for a :class:`Skipped` outcome."""
    return isinstance(x, Skipped)

"""Price inputs by role, and data windows (D-RD-INF-1, D-RD-INF-5).

A run may be given several price files. Each is sniffed for its **native granularity** (the modal
spacing of its own timestamps, never the file order) and assigned a role:

* ``price`` — the finest-grained series (normally hourly). Every study reads this one: oracle
  windows, σ, halts, judgement and the bad-debt bootstrap.
* ``price_daily`` — a daily (or coarser) series *when a finer one is also given*: the long history
  long-horizon consumers want (G3 class C 5-year terms, G4, drawdown evidence). It is put on the
  same hourly grid with its ``filled`` mask, so :func:`ybcal.data.synthetic.fit_returns_of` takes
  observed-to-observed returns at the native (daily) step and the stale-run filter never sees the
  23 forward-filled hours of each day.

With a single price file it is ``price`` whatever its granularity (the behaviour before this
module). Two files competing for one role are an error: the run would otherwise depend on the
order of ``--data`` arguments.

A **window** restricts every price series to a date range before resampling: ``full``,
``last365`` (the 365 days ending at the latest observation of any price input), the named regimes
``2021-22`` and ``2025-26``, or ``YYYY-MM-DD:YYYY-MM-DD`` (either side may be empty). Spreads, depth
and pool-share logs are not windowed (they are short live logs).

Owner: infra (wave 2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from ybcal.data.loaders import PriceSeries, infer_step

#: A native step at or above this many seconds is "daily" (CoinGecko's daily points drift by a few
#: minutes; 20 h keeps a 1-day series daily and a 4-hour one sub-daily).
DAILY_STEP_SECONDS = 20 * 3600

ROLE_FINE = "price"
ROLE_DAILY = "price_daily"
PRICE_ROLES: tuple[str, ...] = (ROLE_FINE, ROLE_DAILY)

DAY = 86400


def _ts(date: str) -> int:
    return int(datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp())


@dataclass(frozen=True)
class Window:
    """A price-data window: absolute ``[start, end)`` unix seconds, or the last ``last_days``."""

    name: str
    start: int | None = None
    end: int | None = None
    last_days: int | None = None

    def bounds(self, latest: int) -> tuple[int | None, int | None]:
        """``(start, end)`` with ``last_days`` resolved against ``latest`` (end exclusive)."""
        if self.last_days is not None:
            return latest - self.last_days * DAY, latest + 1
        return self.start, self.end

    def describe(self, latest: int | None = None) -> str:
        """``2021-22 [2021-01-01, 2023-01-01)``."""
        s, e = self.bounds(latest) if latest is not None else (self.start, self.end)

        def d(t: int | None) -> str:
            return "…" if t is None else datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d")

        return f"{self.name} [{d(s)}, {d(e)})"


#: Named windows (D-RD-INF-5): the full history, the last year, and the two regimes the wave-2
#: briefing names (the 2021-22 bull/bear cycle; the 2025-26 $0.05 → $1.1 → $0.36 cycle).
WINDOWS: dict[str, Window] = {
    "full": Window("full"),
    "last365": Window("last365", last_days=365),
    "2021-22": Window("2021-22", _ts("2021-01-01"), _ts("2023-01-01")),
    "2025-26": Window("2025-26", _ts("2025-01-01"), None),
}


def parse_window(text: str | None) -> Window | None:
    """``None``/``full`` → no window; a name from :data:`WINDOWS`; ``START:END`` dates; ``lastN``
    (N days)."""
    if text is None or text in ("", "full"):
        return None
    if text in WINDOWS:
        return WINDOWS[text]
    if text.startswith("last") and text[4:].isdigit():
        return Window(text, last_days=int(text[4:]))
    if ":" in text:
        a, b = text.split(":", 1)
        try:
            return Window(text, _ts(a) if a else None, _ts(b) if b else None)
        except ValueError as e:
            raise ValueError(f"--window {text!r}: dates must be YYYY-MM-DD ({e})") from None
    raise ValueError(
        f"--window {text!r}: choose from {', '.join(WINDOWS)}, lastN, or YYYY-MM-DD:YYYY-MM-DD"
    )


def window_series(ser: PriceSeries, start: int | None, end: int | None) -> PriceSeries:
    """The rows of ``ser`` with ``start ≤ ts < end``; raises when fewer than 3 remain."""
    m = np.ones(len(ser.ts), dtype=bool)
    if start is not None:
        m &= ser.ts >= start
    if end is not None:
        m &= ser.ts < end
    if int(m.sum()) < 3:
        raise ValueError(
            f"{ser.source}: fewer than 3 observations inside the window "
            f"({int(m.sum())} of {len(ser.ts)})"
        )
    meta = dict(ser.meta)
    meta["window"] = (start, end)
    return PriceSeries(
        ser.ts[m].copy(),
        ser.price_usd[m].copy(),
        ser.source,
        ser.rows_read,
        ser.rows_skipped,
        ser.duplicates,
        ser.conflicting_duplicates,
        None if ser.volume_usd is None else ser.volume_usd[m].copy(),
        meta,
    )


def native_step(ser: PriceSeries) -> int:
    """The series' own modal spacing in seconds."""
    return infer_step(ser.ts)


def step_label(seconds: int) -> str:
    """``1 h``, ``1 d``, ``5 min``."""
    if seconds % DAY == 0:
        return f"{seconds // DAY} d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def assign_roles(series: Sequence[PriceSeries]) -> dict[str, PriceSeries]:
    """Role → series by native granularity (see the module docstring). Raises ``ValueError`` when two
    series compete for one role."""
    if not series:
        return {}
    if len(series) == 1:
        return {ROLE_FINE: series[0]}
    steps = [native_step(s) for s in series]
    fine = [(st, s) for st, s in zip(steps, series, strict=True) if st < DAILY_STEP_SECONDS]
    daily = [(st, s) for st, s in zip(steps, series, strict=True) if st >= DAILY_STEP_SECONDS]

    def clash(role: str, xs: Sequence[tuple[int, PriceSeries]]) -> ValueError:
        what = ", ".join(f"{s.source} ({step_label(st)})" for st, s in xs)
        return ValueError(
            f"two price series for role {role!r}: {what}; pass one of them (a run must not depend on "
            "the order of --data arguments)"
        )

    if len(fine) > 1:
        raise clash(ROLE_FINE, fine)
    if len(daily) > 1:
        raise clash(ROLE_DAILY, daily)
    out: dict[str, PriceSeries] = {}
    if fine:
        out[ROLE_FINE] = fine[0][1]
        if daily:
            out[ROLE_DAILY] = daily[0][1]
    else:  # only one daily series can be here (len(series) > 1 and no clash is impossible)
        out[ROLE_FINE] = daily[0][1]
    return out


def latest_ts(series: Mapping[str, PriceSeries] | Sequence[PriceSeries]) -> int:
    """The latest observation over every price input (the ``last365`` anchor)."""
    xs = series.values() if isinstance(series, Mapping) else series
    return max(int(s.ts[-1]) for s in xs)

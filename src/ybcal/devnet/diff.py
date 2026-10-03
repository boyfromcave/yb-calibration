"""Simulator vs node differential check (PLAN §6.4).

Owner: WP-9.

:func:`compare` lines up two record lists by a key (``height`` for per-block snapshots, ``vault``
for vault rows) and reports, per field, how many keys were compared, how many differ, how many
differences the allowlist excused, and the first mismatching key with both values. The pass
criterion is PLAN §6.4's: exact equality on integer state, an allowlist only for behaviour the
simulator does not model (mempool timing), and no key present on one side only.

:func:`validate_suite` runs the five §6.4 scenarios: each schedule goes to the devnet (through a
``node_runner``) and to the block-mode simulator (a callable), and the per-block records are
compared. While the simulator does not exist (WP-3..5) every scenario reports ``pending``; while
no node can run, ``skipped`` with the reason. Neither ever counts as a pass, so the report's
"validated" badge needs every scenario to pass.

Simulator contract (requested from WP-3..5, ``docs/decisions.md`` D-WP9-5)::

    simulate_devnet(params: ParamSet, path: PricePath, schedule: Schedule) -> list[dict]

returning one record per block from ``params["startHeight"]`` with the fields of
:data:`ybcal.devnet.scrape.HISTORY_FIELDS` (integers; ``None`` for undefined prices), exposed as
``ybcal.sim.engine.simulate_devnet``. :func:`resolve_simulator` picks it up when it lands.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ybcal.devnet.scenarios import SUITE, Schedule, make_schedule, schedule_prices
from ybcal.devnet.status import Skipped
from ybcal.params.paramset import ParamSet
from ybcal.params.paramset import regtest as shipped_regtest
from ybcal.types import PricePath

OWNER_WP = "WP-9"

#: Per-block fields compared by default (PLAN §6.4: prices, σ multiplier, haltMask, activation,
#: supply and collateral totals).
DEFAULT_FIELDS: tuple[str, ...] = (
    "pFast",
    "pMid",
    "pSlow",
    "pMint",
    "pClaim",
    "sigmaMultBps",
    "haltMask",
    "activationCode",
    "signalCount",
    "issuedZat",
    "supplyCents",
    "collateralZat",
    "globalRatioBps",
)
#: Vault fields compared by default (key ``vault``).
VAULT_DIFF_FIELDS: tuple[str, ...] = (
    "status",
    "termClass",
    "collateralZat",
    "mintedCents",
    "lockHeight",
    "claimHeight",
    "closeHeight",
    "burnedCents",
    "unbacked",
)

#: An allowlist entry: ``"field"`` (whole field), ``"field@123"``, ``"field@100-200"``,
#: ``(field, key)`` or ``(field, lo, hi)`` (inclusive, integer keys).
AllowEntry = str | tuple[str, Any] | tuple[str, int, int]

Simulator = Callable[[ParamSet, PricePath, Schedule], list[dict[str, Any]]]
NodeRunner = Callable[[Schedule], "list[dict[str, Any]] | Skipped"]


@dataclass(frozen=True)
class _Allow:
    field: str
    lo: Any = None
    hi: Any = None

    def covers(self, fld: str, key: Any) -> bool:
        if fld != self.field:
            return False
        if self.lo is None:
            return True
        if self.hi is None:
            return bool(key == self.lo)
        return bool(self.lo <= key <= self.hi)


def parse_allowlist(entries: Iterable[AllowEntry]) -> list[_Allow]:
    """Normalise allowlist entries (see :data:`AllowEntry`)."""
    out = []
    for e in entries:
        if isinstance(e, str):
            fld, _, rng = e.partition("@")
            if not rng:
                out.append(_Allow(fld))
            elif "-" in rng.lstrip("-"):
                lo, hi = rng.split("-", 1)
                out.append(_Allow(fld, int(lo), int(hi)))
            else:
                out.append(_Allow(fld, int(rng) if rng.lstrip("-").isdigit() else rng))
        elif len(e) == 2:
            out.append(_Allow(str(e[0]), e[1]))
        elif len(e) == 3:
            out.append(_Allow(str(e[0]), e[1], e[2]))
        else:
            raise ValueError(f"bad allowlist entry {e!r}")
    return out


@dataclass
class FieldDiff:
    """One field's comparison."""

    field: str
    compared: int = 0
    mismatches: int = 0  #: differences not excused by the allowlist
    allowed: int = 0  #: differences the allowlist excused
    first_mismatch: Any = None  #: key of the first non-excused difference
    first_node: Any = None
    first_sim: Any = None

    @property
    def passed(self) -> bool:
        """No unexcused difference."""
        return self.mismatches == 0


@dataclass
class DiffReport:
    """Result of :func:`compare`."""

    key: str
    fields: dict[str, FieldDiff]
    compared_keys: int
    missing_in_sim: list[Any] = field(default_factory=list)
    missing_in_node: list[Any] = field(default_factory=list)
    require_same_keys: bool = True

    @property
    def passed(self) -> bool:
        """Every field matches and (when required) both sides hold the same keys."""
        same = not (self.missing_in_sim or self.missing_in_node) or not self.require_same_keys
        return same and self.compared_keys > 0 and all(f.passed for f in self.fields.values())

    @property
    def first_mismatch(self) -> tuple[Any, str] | None:
        """The earliest (key, field) that differs."""
        hits = [(f.first_mismatch, f.field) for f in self.fields.values() if f.first_mismatch is not None]
        return min(hits, key=lambda x: (str(type(x[0])), x[0])) if hits else None

    def summary(self) -> str:
        """One line per failing field, or ``PASS``."""
        if self.passed:
            return f"PASS ({self.compared_keys} {self.key}s × {len(self.fields)} fields)"
        lines = []
        if self.compared_keys == 0:
            lines.append("nothing compared (no common keys)")
        if self.missing_in_sim:
            lines.append(
                f"{len(self.missing_in_sim)} {self.key}(s) only on the node, first {self.missing_in_sim[0]}"
            )
        if self.missing_in_node:
            lines.append(
                f"{len(self.missing_in_node)} {self.key}(s) only in the simulator, "
                f"first {self.missing_in_node[0]}"
            )
        for f in self.fields.values():
            if not f.passed:
                lines.append(
                    f"{f.field}: {f.mismatches}/{f.compared} differ; first at {self.key} {f.first_mismatch} "
                    f"(node {f.first_node!r}, sim {f.first_sim!r})"
                )
        return "FAIL: " + "; ".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "key": self.key,
            "passed": self.passed,
            "compared_keys": self.compared_keys,
            "missing_in_sim": self.missing_in_sim[:50],
            "missing_in_node": self.missing_in_node[:50],
            "fields": {k: v.__dict__ | {"passed": v.passed} for k, v in self.fields.items()},
        }


def compare(
    node_records: Sequence[Mapping[str, Any]],
    sim_records: Sequence[Mapping[str, Any]],
    fields: Sequence[str] = DEFAULT_FIELDS,
    allowlist: Iterable[AllowEntry] = (),
    *,
    key: str = "height",
    require_same_keys: bool = True,
) -> DiffReport:
    """Compare two record lists field by field, keyed by ``key``.

    Values compare with ``==`` and no tolerance (integers; ``None`` equals only ``None``). A
    field missing from a record compares as ``None``. Duplicate keys raise.
    """
    allow = parse_allowlist(allowlist)

    def index(recs: Sequence[Mapping[str, Any]], side: str) -> dict[Any, Mapping[str, Any]]:
        out: dict[Any, Mapping[str, Any]] = {}
        for r in recs:
            k = r.get(key)
            if k in out:
                raise ValueError(f"duplicate {key} {k!r} in {side} records")
            out[k] = r
        return out

    node, sim = index(node_records, "node"), index(sim_records, "sim")
    common = sorted(set(node) & set(sim), key=lambda k: (str(type(k)), k))
    rep = DiffReport(
        key,
        {f: FieldDiff(f) for f in fields},
        len(common),
        sorted(set(node) - set(sim), key=lambda k: (str(type(k)), k)),
        sorted(set(sim) - set(node), key=lambda k: (str(type(k)), k)),
        require_same_keys,
    )
    for k in common:
        a, b = node[k], sim[k]
        for f in fields:
            fd = rep.fields[f]
            fd.compared += 1
            va, vb = a.get(f), b.get(f)
            if va == vb and isinstance(va, bool) == isinstance(vb, bool):
                continue
            if any(x.covers(f, k) for x in allow):
                fd.allowed += 1
                continue
            fd.mismatches += 1
            if fd.first_mismatch is None:
                fd.first_mismatch, fd.first_node, fd.first_sim = k, va, vb
    return rep


# ---------------------------------------------------------------------------------------------------
# The suite

ScenarioStatus = Literal["pass", "fail", "pending", "skipped", "error"]


@dataclass
class ScenarioResult:
    """One scenario of the suite."""

    name: str
    status: ScenarioStatus
    reason: str = ""
    report: DiffReport | None = None
    blocks: int = 0


@dataclass
class SuiteReport:
    """The §6.4 suite."""

    results: list[ScenarioResult]

    @property
    def validated(self) -> bool:
        """The report's "validated" badge: every scenario ran and passed."""
        return bool(self.results) and all(r.status == "pass" for r in self.results)

    @property
    def failed(self) -> bool:
        """Some scenario ran and mismatched (or errored)."""
        return any(r.status in ("fail", "error") for r in self.results)

    def summary(self) -> str:
        """One line per scenario."""
        lines = [
            f"{r.name:<20} {r.status.upper():<8} {r.reason or (r.report.summary() if r.report else '')}"
            for r in self.results
        ]
        badge = "VALIDATED" if self.validated else "NOT VALIDATED"
        return "\n".join([*lines, f"suite: {badge}"])

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "validated": self.validated,
            "results": [
                {
                    "name": r.name,
                    "status": r.status,
                    "reason": r.reason,
                    "blocks": r.blocks,
                    "report": r.report.to_dict() if r.report else None,
                }
                for r in self.results
            ],
        }


def resolve_simulator() -> Simulator | None:
    """``ybcal.sim.engine.simulate_devnet`` once WP-3..5 provide it, else ``None``."""
    try:
        mod = importlib.import_module("ybcal.sim.engine")
    except ImportError:
        return None
    fn = getattr(mod, "simulate_devnet", None)
    return fn if callable(fn) else None


def validate_suite(
    scenarios: Sequence[str] = SUITE,
    simulator: Simulator | None = None,
    *,
    params: ParamSet | None = None,
    node_runner: NodeRunner | None = None,
    allowlist: Iterable[AllowEntry] = (),
    fields: Sequence[str] = DEFAULT_FIELDS,
    seed: int = 0,
    node_skip_reason: str = "no devnet runner (no ycashd binary)",
) -> SuiteReport:
    """Run the differential suite.

    For each scenario the schedule is built from ``params`` (default: the shipped regtest column);
    with no ``simulator`` the scenario is ``pending WP-3..5``; with no ``node_runner`` (or when it
    returns :class:`Skipped`) it is ``skipped``; otherwise node and simulator records are compared.
    """
    ps = params or shipped_regtest()
    allow = list(allowlist)
    out: list[ScenarioResult] = []
    for name in scenarios:
        try:
            sched = make_schedule(name, ps, seed=seed)
        except Exception as e:
            out.append(ScenarioResult(name, "error", f"schedule: {e}"))
            continue
        if simulator is None:
            out.append(
                ScenarioResult(
                    name,
                    "pending",
                    "pending WP-3..5: no block-mode simulator (ybcal.sim.engine.simulate_devnet) yet",
                    blocks=sched.total_blocks,
                )
            )
            continue
        if node_runner is None:
            out.append(
                ScenarioResult(name, "skipped", f"skipped: {node_skip_reason}", blocks=sched.total_blocks)
            )
            continue
        try:
            node = node_runner(sched)
            if isinstance(node, Skipped):
                out.append(ScenarioResult(name, "skipped", str(node), blocks=sched.total_blocks))
                continue
            sim = simulator(ps, schedule_prices(sched), sched)
        except Exception as e:
            out.append(ScenarioResult(name, "error", f"{type(e).__name__}: {e}", blocks=sched.total_blocks))
            continue
        rep = compare(node, sim, fields, allow)
        out.append(ScenarioResult(name, "pass" if rep.passed else "fail", "", rep, sched.total_blocks))
    return SuiteReport(out)

"""Immutable parameter sets keyed by registry name (owner: WP-0).

A :class:`ParamSet` holds a value for every registry entry (fields *and* header constants), so a
study, the simulator and the report all address parameters by the same names. ``network`` is an
ordinary key: ``"main"`` / ``"test"`` / ``"regtest"`` for sets read from source, ``"candidate"``
for mainnet-scale sets a study proposes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from ybcal.params.registry import REGISTRY, ParamSpec
from ybcal.types import ParamValue

if TYPE_CHECKING:
    from ybcal.params.invariants import Context, Violation

#: Networks whose block counts are at regtest scale (everything else is mainnet scale).
REGTEST_SCALE_NETWORKS: frozenset[str] = frozenset({"regtest"})


def _coerce(spec: ParamSpec, value: Any) -> ParamValue:
    """Type-check one value against its spec (ints stay ints, bools stay bools)."""
    if spec.unit == "bool":
        if not isinstance(value, bool):
            raise TypeError(f"{spec.name} must be bool, got {value!r}")
        return value
    if spec.unit in ("enum", "hex", "string"):
        if not isinstance(value, str):
            raise TypeError(f"{spec.name} must be str, got {value!r}")
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{spec.name} must be an integer ({spec.unit}), got {value!r}")
    return value


class ParamSet(Mapping[str, ParamValue]):
    """An immutable, fully-populated parameter set.

    Build one with :func:`mainnet`, :func:`regtest`, :meth:`from_json`, or ``base.replace(...)``.
    """

    __slots__ = ("_hash", "_values")

    def __init__(self, values: Mapping[str, ParamValue]) -> None:
        missing = [k for k in REGISTRY if k not in values]
        extra = [k for k in values if k not in REGISTRY]
        if missing or extra:
            raise KeyError(f"ParamSet keys differ from the registry: missing={missing} extra={extra}")
        self._values: dict[str, ParamValue] = {k: _coerce(REGISTRY[k], values[k]) for k in REGISTRY}
        self._hash: int | None = None

    # Mapping protocol ----------------------------------------------------------------------------
    def __getitem__(self, key: str) -> ParamValue:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ParamSet):
            return self._values == other._values
        return NotImplemented

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(tuple(self._values.items()))
        return self._hash

    def __repr__(self) -> str:
        return f"ParamSet(network={self.network!r}, sha={self.digest()[:12]})"

    # Convenience ---------------------------------------------------------------------------------
    @property
    def network(self) -> str:
        """The ``network`` value."""
        return str(self._values["network"])

    @property
    def is_regtest_scale(self) -> bool:
        """True when block counts are at regtest scale (scale-dependent invariants are skipped)."""
        return self.network in REGTEST_SCALE_NETWORKS

    def as_int(self, key: str) -> int:
        """``self[key]`` as an int (type-checked)."""
        v = self._values[key]
        if isinstance(v, bool) or not isinstance(v, int):
            raise TypeError(f"{key} is not an integer parameter")
        return v

    def array(self, field: str) -> tuple[int, ...]:
        """An array field as a tuple: ``ps.array("baseRatioBps") == (50000, 40000, 30000)``."""
        out, i = [], 0
        while f"{field}[{i}]" in self._values:
            out.append(self.as_int(f"{field}[{i}]"))
            i += 1
        if not out:
            raise KeyError(f"{field} is not an array field")
        return tuple(out)

    # Derivation ----------------------------------------------------------------------------------
    def replace(self, changes: Mapping[str, ParamValue] | None = None, /, **kw: ParamValue) -> ParamSet:
        """A new set with ``changes`` applied; derived values are recomputed from their parents
        unless explicitly given. Array fields go through ``changes`` (``{"classMin[0]": …}``)."""
        upd: dict[str, ParamValue] = dict(changes or {})
        upd.update(kw)
        for k in upd:
            if k not in REGISTRY:
                raise KeyError(f"unknown parameter {k!r}")
        vals = dict(self._values)
        vals.update(upd)
        for name, spec in REGISTRY.items():
            if spec.derive is not None and name not in upd:
                vals[name] = spec.derive(vals)
        return ParamSet(vals)

    def derived_mismatches(self) -> dict[str, tuple[ParamValue, ParamValue]]:
        """Derived values that differ from their formula: name → (held, formula)."""
        out = {}
        for name, spec in REGISTRY.items():
            if spec.derive is not None:
                want = spec.derive(self._values)
                if want != self._values[name]:
                    out[name] = (self._values[name], want)
        return out

    def check(self, context: Context | None = None) -> list[Violation]:
        """Every PLAN §1.4 invariant that fails on this set (empty list = admissible)."""
        from ybcal.params.invariants import check_all

        return check_all(self, context)

    def diff(self, other: Mapping[str, ParamValue]) -> dict[str, tuple[ParamValue, ParamValue]]:
        """Keys whose values differ: name → (self value, other value), registry order."""
        return {k: (v, other[k]) for k, v in self._values.items() if k in other and other[k] != v}

    def delta(self, base: Mapping[str, ParamValue]) -> dict[str, ParamValue]:
        """The values of this set that differ from ``base`` (name → this value)."""
        return {k: v for k, v in self._values.items() if base.get(k) != v}

    # Serialisation -------------------------------------------------------------------------------
    def to_dict(self) -> dict[str, ParamValue]:
        """Plain dict in registry order."""
        return dict(self._values)

    def to_json(self, *, indent: int | None = 2) -> str:
        """Canonical JSON: ``{"format": "ybcal-paramset/1", "values": {...}}``."""
        return json.dumps({"format": "ybcal-paramset/1", "values": self._values}, indent=indent) + (
            "\n" if indent is not None else ""
        )

    def digest(self) -> str:
        """sha256 of the compact canonical JSON (stable across runs; used as an overlay key)."""
        blob = json.dumps(self._values, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ParamSet:
        """From a plain value dict or a ``ybcal-paramset/1`` document."""
        if d.get("format") == "ybcal-paramset/1":
            d = d["values"]
        return cls(d)

    @classmethod
    def from_json(cls, text: str) -> ParamSet:
        """Inverse of :meth:`to_json` (also accepts a plain value dict)."""
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_extracted(cls, extracted: Any, network: str) -> ParamSet:
        """From a :class:`ybcal.params.extract.Extracted` network column (fields + constants)."""
        vals = extracted.values(network)
        return cls({k: vals[k] for k in REGISTRY})


def mainnet() -> ParamSet:
    """The registry's mainnet column (the shipped set at the pin)."""
    return ParamSet({k: s.mainnet for k, s in REGISTRY.items()})


def regtest() -> ParamSet:
    """The registry's regtest column (flags at ``REGTEST_FLAG_DEFAULTS``)."""
    return ParamSet({k: s.regtest for k, s in REGISTRY.items()})


def candidate(base: ParamSet | None = None, **changes: ParamValue) -> ParamSet:
    """A mainnet-scale candidate (``network="candidate"``) from ``base`` (default mainnet)."""
    return (base or mainnet()).replace({"network": "candidate", **changes})

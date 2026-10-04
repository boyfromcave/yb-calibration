"""Regtest parameter overlay → runtime flags + a ``RegtestParams()`` patch (PLAN §6.2).

Owner: WP-9.

An *overlay* is a regtest-scale :class:`~ybcal.params.paramset.ParamSet`. It splits into

* the **six runtime flags** ``ParamsFromArgs`` reads (``src/yellowback/index.cpp:1229-1259`` at the
  pin): ``-yellowbackstartheight``, ``-yellowbacksigmaref``, ``-yellowbacksupplycapbps``,
  ``-yellowbackenforceuntil``, ``-yellowbackattestarmmin``, ``-yellowbackbundlecarrier`` — varied
  without a rebuild;
* **compiled values** — every other field that differs from the shipped regtest column; they need
  a patch to ``RegtestParams()`` in ``src/yellowback/params.cpp`` and a rebuild.

The patch is generated as a unified diff against the file at the pinned commit and applied only
inside a ``.work/`` worktree (:func:`apply_patch`); nothing here writes to the ycash6 clone.
Header constants (``params.h``) and identity fields cannot be overlaid.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ybcal.params.paramset import ParamSet
from ybcal.params.paramset import regtest as shipped_regtest
from ybcal.params.registry import PARAMS_CPP, PINNED_COMMIT, REGISTRY
from ybcal.params.scaling import check_regtest
from ybcal.types import ParamValue
from ybcal.units import COIN

OWNER_WP = "WP-9"

#: registry field → node flag, in ``ParamsFromArgs`` order.
RUNTIME_FLAGS: dict[str, str] = {
    "startHeight": "-yellowbackstartheight",
    "sigmaRefBps": "-yellowbacksigmaref",
    "supplyCapBps": "-yellowbacksupplycapbps",
    "enforceUntilHeight": "-yellowbackenforceuntil",
    "attestArmMin": "-yellowbackattestarmmin",
    "bundleCarrier": "-yellowbackbundlecarrier",
}

#: ``BundleCarrier`` enum name → the flag's spelling (``ParseBundleCarrier``).
CARRIER_FLAG_VALUES: dict[str, str] = {"SCRIPTSIG": "scriptsig", "OP_RETURN": "opreturn", "EITHER": "either"}

#: Fields an overlay may never change.
IMMUTABLE: frozenset[str] = frozenset({"network", "addressVersion", "tokenValue", "refWindow"})

_I32 = 0x7FFFFFFF


class OverlayError(ValueError):
    """The overlay cannot be expressed as runtime flags plus a ``RegtestParams()`` patch."""


@dataclass(frozen=True)
class OverlaySplit:
    """An overlay split into runtime flags and compiled values."""

    params: ParamSet  #: the full regtest-scale overlay set
    runtime: dict[str, ParamValue]  #: the six flag fields → value
    compiled: dict[str, ParamValue]  #: compiled fields that differ from the base column
    base_digest: str  #: digest of the base column compared against
    notes: list[str] = field(default_factory=list)

    @property
    def needs_build(self) -> bool:
        """True when compiled values change (a stock binary cannot run this overlay)."""
        return bool(self.compiled)

    def node_args(self) -> list[str]:
        """The six ``-flag=value`` node arguments (always all six, so runs are explicit)."""
        return runtime_args(self.runtime)

    def conf_lines(self) -> list[str]:
        """The same as ``ycash.conf`` lines (``name=value``)."""
        return [a.lstrip("-") for a in self.node_args()]


def runtime_args(runtime: Mapping[str, ParamValue]) -> list[str]:
    """``-yellowback…=value`` for each runtime flag, validated like ``ParamsFromArgs``."""
    out = []
    for fld, flag in RUNTIME_FLAGS.items():
        v = runtime[fld]
        if fld == "bundleCarrier":
            if v not in CARRIER_FLAG_VALUES:
                raise OverlayError(f"bundleCarrier {v!r} is not one of {sorted(CARRIER_FLAG_VALUES)}")
            out.append(f"{flag}={CARRIER_FLAG_VALUES[str(v)]}")
            continue
        iv = int(v)
        lo = 1 if fld == "startHeight" else 0
        hi = 10_000 if fld == "supplyCapBps" else _I32
        if not lo <= iv <= hi:
            raise OverlayError(f"{flag}={iv} is outside [{lo}, {hi}] (ParamsFromArgs would refuse it)")
        out.append(f"{flag}={iv}")
    return out


# ---------------------------------------------------------------------------------------------------
# Loading


def load_overlay(src: str | Path | Mapping[str, Any] | ParamSet, base: ParamSet | None = None) -> ParamSet:
    """An overlay from a file path, a mapping or a ParamSet.

    Accepted documents: a report's ``recommended.json`` (``ybcal-extract/1``: its mainnet column,
    also given as the report directory), ``ybcal-paramset/1`` (full set), ``ybcal-scaled/1`` (the
    scaler's output),
    ``ybcal-overlay/1`` (``{"values": {delta}}``), a plain full value dict, or a plain delta dict
    applied over ``base`` (default: the shipped regtest column; derived values recomputed).
    A mainnet-scale set is returned as is — :func:`split` refuses it; scale it first with
    :func:`ybcal.params.scaling.scale_to_regtest`.
    """
    if isinstance(src, ParamSet):
        return src
    if isinstance(src, str | Path) and Path(src).is_dir():  # a report directory
        src = Path(src) / "recommended.json"
    doc: Mapping[str, Any] = json.loads(Path(src).read_text()) if isinstance(src, str | Path) else src
    fmt = doc.get("format")
    if fmt == "ybcal-extract/1":  # a report's recommended.json: its mainnet column (scaled by the caller)
        from ybcal.params.extract import Extracted

        return ParamSet.from_extracted(Extracted.from_dict(dict(doc)), "main")
    if fmt in ("ybcal-paramset/1", "ybcal-scaled/1", "ybcal-overlay/1"):
        values = dict(doc["values"])
    elif fmt is None:
        values = dict(doc)
    else:
        raise OverlayError(f"unknown overlay format {fmt!r}")
    if all(k in values for k in REGISTRY):
        return ParamSet(values)
    unknown = [k for k in values if k not in REGISTRY]
    if unknown:
        raise OverlayError(f"unknown parameter(s) in overlay: {unknown}")
    return (base or shipped_regtest()).replace(values)


# ---------------------------------------------------------------------------------------------------
# Splitting


def split(overlay: ParamSet, base: ParamSet | None = None) -> OverlaySplit:
    """Split a regtest-scale overlay into runtime flags and compiled differences from ``base``.

    ``base`` is the shipped regtest column (registry, or an extraction at the worktree commit).
    Raises :class:`OverlayError` for a mainnet-scale set, a changed header constant or identity
    field, or a set that fails a regtest-scale invariant.
    """
    if not overlay.is_regtest_scale:
        raise OverlayError(
            f"overlay network is {overlay.network!r}, not regtest: scale it first "
            "(ybcal.params.scaling.scale_to_regtest)"
        )
    ref = base or shipped_regtest()
    runtime = {k: overlay[k] for k in RUNTIME_FLAGS}
    compiled: dict[str, ParamValue] = {}
    refused: list[str] = []
    for name, (val, _old) in overlay.diff(ref).items():
        spec = REGISTRY[name]
        if name in RUNTIME_FLAGS:
            continue
        if name in IMMUTABLE or spec.origin == "header" or spec.klass == "meta":
            refused.append(name)
            continue
        compiled[name] = val
    if refused:
        raise OverlayError(f"overlay changes value(s) that cannot be patched in RegtestParams(): {refused}")
    violations = check_regtest(overlay)
    if violations:
        raise OverlayError(
            "overlay violates: " + "; ".join(f"{v.invariant}: {v.message}" for v in violations)
        )
    runtime_args(runtime)  # validate ranges like ParamsFromArgs
    notes = []
    held = overlay.derived_mismatches()
    if held:
        notes.append(f"derived values held off their formula: {sorted(held)}")
    return OverlaySplit(
        params=overlay, runtime=runtime, compiled=compiled, base_digest=ref.digest(), notes=notes
    )


def overlay_hash(overlay: ParamSet, commit: str = PINNED_COMMIT) -> str:
    """sha256 over the commit and the whole overlay (runtime flags included): the run key."""
    blob = json.dumps({"commit": commit, "values": overlay.to_dict()}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def build_key(sp: OverlaySplit, commit: str = PINNED_COMMIT) -> str:
    """Binary cache key: the commit plus the *compiled* values only (flags need no rebuild).

    ``stock-<commit12>`` when nothing compiled changes, else ``ov-<sha256[:16]>``.
    """
    if not sp.compiled:
        return f"stock-{commit[:12]}"
    blob = json.dumps({"commit": commit, "compiled": sp.compiled}, sort_keys=True, separators=(",", ":"))
    return "ov-" + hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------------
# Patch generation


def _cpp_value(name: str, value: ParamValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        raise OverlayError(f"{name}: string values cannot be compiled ({value!r})")
    if REGISTRY[name].unit == "zat" and value % COIN == 0 and value >= COIN:
        return f"{value // COIN} * COIN"
    return str(int(value))


def _regtest_body_span(src: str) -> tuple[int, int]:
    """[start, end) of the body of ``Params RegtestParams(...) { ... }`` (inside the braces)."""
    m = re.search(r"Params\s+RegtestParams\s*\([^)]*\)\s*\{", src)
    if not m:
        raise OverlayError("RegtestParams() definition not found in params.cpp")
    depth, i = 1, m.end()
    while i < len(src) and depth:
        if src.startswith("//", i):
            i = src.index("\n", i)
            continue
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth:
        raise OverlayError("unbalanced braces in RegtestParams()")
    return m.end(), i - 1


def patch_source(src: str, compiled: Mapping[str, ParamValue], tag: str = "") -> str:
    """``params.cpp`` text with ``compiled`` written into ``RegtestParams()`` only.

    An existing ``r.<field> = …;`` assignment has its value replaced in place; a field
    ``RegtestParams()`` does not set (it inherits ``SetCommon``) gets a new assignment inserted
    before the ``// the six flags`` block (or before ``return r;``).
    """
    lo, hi = _regtest_body_span(src)
    body = src[lo:hi]
    added: list[str] = []
    for name in sorted(compiled, key=lambda n: list(REGISTRY).index(n)):
        val = _cpp_value(name, compiled[name])
        pat = re.compile(r"(\br\." + re.escape(name) + r"\s*=\s*)([^;]+)(;)")
        hits = pat.findall(body)
        if len(hits) > 1:
            raise OverlayError(f"{name} is assigned {len(hits)} times in RegtestParams()")
        if hits:
            body = pat.sub(lambda m, v=val: m.group(1) + v + m.group(3), body, count=1)
        else:
            added.append(
                f"    r.{name} = {val};" + (f"   // ybcal overlay {tag}" if tag else "   // ybcal overlay")
            )
    if added:
        anchor = re.search(r"^[ \t]*// the six flags", body, re.M) or re.search(
            r"^[ \t]*return r;", body, re.M
        )
        if not anchor:
            raise OverlayError("no insertion point in RegtestParams()")
        body = body[: anchor.start()] + "\n".join(added) + "\n" + body[anchor.start() :]
    return src[:lo] + body + src[hi:]


def make_patch(src: str, compiled: Mapping[str, ParamValue], *, path: str = PARAMS_CPP, tag: str = "") -> str:
    """Unified diff (``a/``/``b/`` prefixes, ``git apply`` -p1) of :func:`patch_source`. Empty when
    nothing changes."""
    new = patch_source(src, compiled, tag)
    if new == src:
        return ""
    lines = difflib.unified_diff(
        src.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(lines)


def read_params_cpp(repo: str | Path, commit: str = PINNED_COMMIT) -> str:
    """``params.cpp`` at ``commit`` via ``git show`` (never the working tree)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{PARAMS_CPP}"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise OverlayError(f"git show {commit}:{PARAMS_CPP} failed: {proc.stderr.strip()}")
    return proc.stdout


def apply_patch(worktree: str | Path, patch: str, *, check_only: bool = False) -> None:
    """``git apply [--check]`` the patch inside a ``.work`` worktree (never the clone itself)."""
    if not patch:
        return
    args = ["git", "-C", str(worktree), "apply", "--whitespace=nowarn"]
    if check_only:
        args.append("--check")
    proc = subprocess.run([*args, "-"], input=patch, capture_output=True, text=True)
    if proc.returncode != 0:
        raise OverlayError(f"git apply{' --check' if check_only else ''} failed: {proc.stderr.strip()}")


def patched_worktree_state(worktree: str | Path) -> str:
    """``git status --porcelain`` of the worktree (what an applied overlay changed)."""
    return subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"], capture_output=True, text=True
    ).stdout

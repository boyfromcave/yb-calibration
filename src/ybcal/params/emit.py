"""Recommended set → ``recommended.json`` + a ``params.cpp`` patch (PLAN §7).

Owner: WP-8.

* :func:`recommended_document` — the recommended set in the shape of ``ybcal params extract``
  (``ybcal-extract/1``): ``networks.main`` holds the recommended mainnet column, ``test`` the
  testnet column with the same ``SetCommon()`` changes, ``regtest`` the shipped regtest column
  unchanged (the tool never proposes regtest values). ``Extracted.from_dict`` reads it back.
* :func:`make_patch` — unified diffs against ``src/yellowback/params.cpp`` at the pin. Only lines
  inside ``SetCommon()`` and ``MainParams()`` are touched; each changed line gets a
  ``// ybcal: … (report §…)`` comment naming the report section. Locked (consensus) changes go in
  the main patch; excluded (patch-release) field changes in a separate patch; header constants
  (``params.h``, e.g. ``DEFAULT_REF_LAG``) are listed, never patched here.
* :func:`check_patch` — ``git apply --check`` in a throwaway detached worktree of a ycash6 clone
  under ``.work/`` (removed and pruned afterwards; ycash6 branches are never touched).

The source text comes from ``git show <pin>:src/yellowback/params.cpp`` when a clone is available,
else from the vendored copy ``params_cpp_7702d22.json`` (same bytes; sha256 recorded).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ybcal.params.paramset import ParamSet
from ybcal.params.registry import PARAMS_CPP, PINNED_COMMIT, REGISTRY
from ybcal.types import ParamValue
from ybcal.units import COIN

OWNER_WP = "WP-8"

#: Vendored ``params.cpp`` at the pin (package data).
VENDORED_PARAMS_CPP = Path(__file__).with_name(f"params_cpp_{PINNED_COMMIT}.json")
#: Functions whose lines the patch may change.
PATCHABLE_FUNCTIONS: tuple[str, ...] = ("SetCommon", "MainParams")


class EmitError(RuntimeError):
    """The patch could not be generated (e.g. a field's line was not found)."""


# ---------------------------------------------------------------------------------------------------
# Source


@dataclass(frozen=True)
class Source:
    """``params.cpp`` text at a commit and where it came from."""

    text: str
    commit: str
    origin: str  #: ``"git show <repo>"`` or ``"vendored"``
    sha256: str


def vendored_params_cpp() -> Source:
    """The packaged copy of ``params.cpp`` at the pin."""
    d = json.loads(VENDORED_PARAMS_CPP.read_text())
    text = d["text"]
    if hashlib.sha256(text.encode()).hexdigest() != d["sha256"]:
        raise EmitError(f"{VENDORED_PARAMS_CPP.name} is corrupt (sha256 mismatch)")
    return Source(text, d["commit"], "vendored", d["sha256"])


def params_cpp_source(ycash6: str | Path | None = None, ref: str = PINNED_COMMIT) -> Source:
    """``git show ref:src/yellowback/params.cpp`` from ``ycash6`` (read-only), else the vendored copy
    (only valid for the pin)."""
    if ycash6 is not None and (Path(ycash6) / ".git").exists():
        try:
            raw = subprocess.run(
                ["git", "-C", str(ycash6), "show", f"{ref}:{PARAMS_CPP}"], check=True, capture_output=True
            ).stdout
            commit = subprocess.run(
                ["git", "-C", str(ycash6), "rev-parse", ref], check=True, capture_output=True, text=True
            ).stdout.strip()
            return Source(raw.decode(), commit, f"git show {ycash6}", hashlib.sha256(raw).hexdigest())
        except (subprocess.CalledProcessError, OSError):
            pass
    if ref != PINNED_COMMIT:
        raise EmitError(f"no ycash6 clone to read {ref}; the vendored copy is at {PINNED_COMMIT}")
    return vendored_params_cpp()


# ---------------------------------------------------------------------------------------------------
# recommended.json


def _column(values: Mapping[str, ParamValue], network: str, fields: list[str]) -> dict[str, ParamValue]:
    out = {k: values[k] for k in fields if k in values}
    out["network"] = network
    return out


def recommended_document(
    recommended: ParamSet, *, base: ParamSet | None = None, extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The ``ybcal-extract/1`` document for ``recommended`` (see module docstring)."""
    from ybcal.params.extract import load_snapshot

    snap = load_snapshot()
    fields = list(snap.fields)
    main = _column(recommended, "main", fields)
    # SetCommon() is shared with testnet: apply the same field changes to the test column.
    shipped_main = snap.networks["main"]
    test = dict(snap.networks["test"])
    for k, v in main.items():
        if k in ("network", "startHeight", "enforceUntilHeight", "addressVersion"):
            continue
        if shipped_main.get(k) != v:
            test[k] = v
    constants = dict(snap.constants)
    for k, spec in REGISTRY.items():
        if spec.origin == "header" and k in constants and recommended[k] != constants[k]:
            constants[k] = recommended[k]  # type: ignore[assignment]
    doc: dict[str, Any] = {
        "format": "ybcal-extract/1",
        "ref": f"ybcal-recommended@{recommended.digest()[:12]}",
        "commit": snap.commit,
        "fields": fields,
        "constants": constants,
        "regtest_flags": dict(snap.regtest_flags),
        "networks": {"main": main, "test": test, "regtest": dict(snap.networks["regtest"])},
    }
    b = base if base is not None else None
    doc["ybcal"] = {
        "kind": "recommended",
        "base_commit": snap.commit,
        "changes": {
            k: {"current": b[k], "recommended": recommended[k]}
            for k in REGISTRY
            if b is not None and b[k] != recommended[k] and k != "network"
        },
        **dict(extra or {}),
    }
    return doc


def write_recommended(
    path: str | Path,
    recommended: ParamSet,
    *,
    base: ParamSet | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Path:
    """Write ``recommended.json``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(recommended_document(recommended, base=base, extra=extra), indent=2, default=str) + "\n"
    )
    return p


# ---------------------------------------------------------------------------------------------------
# The patch


def cpp_literal(name: str, value: ParamValue, old_expr: str = "") -> str:
    """C++ right-hand side for ``value`` (keeps ``N * COIN`` style when the old one used it)."""
    spec = REGISTRY[name]
    if spec.unit == "bool":
        return "true" if value else "false"
    if spec.unit == "enum":
        return f"BundleCarrier::{value}"
    if isinstance(value, int) and "COIN" in old_expr and value % COIN == 0:
        return f"{value // COIN} * COIN"
    return str(value)


def _function_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    """``{function: (first, last)}`` line index ranges of SetCommon() and MainParams() bodies."""
    spans: dict[str, tuple[int, int]] = {}
    for fn in PATCHABLE_FUNCTIONS:
        pat = re.compile(rf"\b{fn}\s*\(")
        start = next(
            (i for i, ln in enumerate(lines) if pat.search(ln) and not ln.rstrip().endswith(";")), None
        )
        if start is None:
            continue
        depth, opened = 0, False
        for j in range(start, len(lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            opened = opened or "{" in lines[j]
            if opened and depth <= 0:
                spans[fn] = (start, j)
                break
    return spans


def _assign_re(var: str, field_name: str) -> re.Pattern[str]:
    return re.compile(rf"(\b{var}\.{re.escape(field_name)}\s*=\s*)([^;]+?)(\s*;)")


@dataclass
class PatchChange:
    """One value changed in the patch."""

    param: str
    current: ParamValue
    recommended: ParamValue
    function: str
    line: int  #: 1-based line in params.cpp at the pin
    change_path: str
    section: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready."""
        return {
            "param": self.param,
            "current": self.current,
            "recommended": self.recommended,
            "function": self.function,
            "line": self.line,
            "change_path": self.change_path,
            "section": self.section,
        }


@dataclass
class PatchResult:
    """Output of :func:`make_patch`."""

    locked: str  #: unified diff (SetCommon/MainParams consensus changes); "" if none
    patch_release: str  #: unified diff of excluded (patch-release) field changes; "" if none
    changes: list[PatchChange] = field(default_factory=list)
    release_changes: list[PatchChange] = field(default_factory=list)
    header_changes: list[dict[str, Any]] = field(default_factory=list)  #: params.h constants (not patched)
    unpatched: list[str] = field(default_factory=list)  #: fields not found in the bodies
    source: Source | None = None

    def summary(self) -> str:
        """One line."""
        return (
            f"{len(self.changes)} locked/per-release line change(s), {len(self.release_changes)} "
            f"patch-release, {len(self.header_changes)} header constant(s) listed only"
        )


def _apply(
    lines: list[str],
    spans: Mapping[str, tuple[int, int]],
    items: list[tuple[str, ParamValue]],
    base: Mapping[str, ParamValue],
    sections: Mapping[str, str],
    tag: str,
    unpatched: list[str],
) -> tuple[list[str], list[PatchChange]]:
    out = list(lines)
    notes: dict[int, list[str]] = {}
    changes: list[PatchChange] = []
    for name, new in items:
        spec = REGISTRY[name]
        fn = "MainParams" if name in ("startHeight", "enforceUntilHeight") else "SetCommon"
        var = "m" if fn == "MainParams" else "p"
        if fn not in spans:
            unpatched.append(name)
            continue
        a, b = spans[fn]
        rx = _assign_re(var, name)
        hit = None
        for i in range(a, b + 1):
            code = out[i].split("//", 1)[0]
            m = rx.search(code)
            if m:
                hit = (i, m)
                break
        if hit is None:
            unpatched.append(name)
            continue
        i, m = hit
        old_expr = m.group(2).strip()
        lit = cpp_literal(name, new, old_expr)
        code, sep, comment = out[i].partition("//")
        code = code[: m.start(2)] + lit + code[m.end(2) :]
        out[i] = code + sep + comment
        sec = sections.get(name, "")
        notes.setdefault(i, []).append(f"{name} {base[name]} -> {new}" + (f" (report {sec})" if sec else ""))
        changes.append(
            PatchChange(
                name, base[name], new, fn, i + 1, spec.change_path if tag != "derived" else "locked", sec
            )
        )
    for i, ns in notes.items():
        text = out[i].rstrip("\n")
        nl = "\n" if out[i].endswith("\n") else ""
        lead = " |" if "//" in text else "  //"
        out[i] = f"{text}{lead} ybcal{'' if tag == 'locked' else ' ' + tag}: {'; '.join(ns)}{nl}"
    return out, changes


def _diff(old: list[str], new: list[str], path: str) -> str:
    if old == new:
        return ""
    body = "".join(difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", n=3))
    return f"diff --git a/{path} b/{path}\n{body}"


def make_patch(
    recommended: Mapping[str, ParamValue],
    base: Mapping[str, ParamValue],
    *,
    sections: Mapping[str, str] | None = None,
    source: Source | None = None,
) -> PatchResult:
    """Diffs that turn the shipped ``params.cpp`` into ``recommended`` (see module docstring).

    ``sections`` maps a parameter to its report section label (e.g. ``"§3.12"``) for the line
    comments. Derived values (min-fills, ``qHighBps``, ``attestMaxAge``, ``recapRatioBps``,
    ``volPeriodsPerYear``) are written with their parent's change.
    """
    src = source or params_cpp_source()
    lines = src.text.splitlines(keepends=True)
    spans = _function_spans(lines)
    secs = dict(sections or {})
    locked: list[tuple[str, ParamValue]] = []
    release: list[tuple[str, ParamValue]] = []
    header: list[dict[str, Any]] = []
    for name, spec in REGISTRY.items():
        if name == "network" or recommended[name] == base[name]:
            continue
        if spec.origin == "header":
            header.append(
                {
                    "param": name,
                    "current": base[name],
                    "recommended": recommended[name],
                    "change_path": spec.change_path,
                    "file": "src/yellowback/params.h",
                }
            )
            continue
        if spec.klass in ("meta", "constant"):
            continue
        (release if spec.change_path == "patch-release" else locked).append((name, recommended[name]))
    unpatched: list[str] = []
    new_locked, ch = _apply(lines, spans, locked, base, secs, "locked", unpatched)
    new_release, rch = _apply(lines, spans, release, base, secs, "patch-release", unpatched)
    path = PARAMS_CPP
    return PatchResult(
        _diff(lines, new_locked, path), _diff(lines, new_release, path), ch, rch, header, unpatched, src
    )


# ---------------------------------------------------------------------------------------------------
# Verification


@dataclass(frozen=True)
class PatchCheck:
    """Outcome of :func:`check_patch`."""

    status: str  #: ``"applies"`` / ``"fails"`` / ``"skipped"`` / ``"empty"``
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Applies (or nothing to apply)."""
        return self.status in ("applies", "empty")


def check_patch(patch_text: str, ycash6: str | Path | None = None, commit: str = PINNED_COMMIT) -> PatchCheck:
    """``git apply --check`` of ``patch_text`` in a temporary detached worktree of ``ycash6`` at
    ``commit`` (under the work dir, ``$YBCAL_WORK`` or ``.work/``; removed and pruned afterwards).
    ``skipped`` without a clone."""
    if not patch_text.strip():
        return PatchCheck("empty", "no changes")
    from ybcal.devnet.worktree import temp_worktree, ycash6_repo

    repo = ycash6_repo(ycash6)
    if repo is None:
        return PatchCheck("skipped", "no ycash6 clone (pass --ycash6 or set YBCAL_YCASH6)")
    try:
        with temp_worktree(
            repo,
            commit,
            name=f"tmp-patchcheck-{commit[:12]}-{hashlib.sha256(patch_text.encode()).hexdigest()[:8]}",
        ) as wt:
            with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
                fh.write(patch_text)
                pfile = fh.name
            try:
                r = subprocess.run(
                    ["git", "-C", str(wt.path), "apply", "--check", pfile], capture_output=True, text=True
                )
            finally:
                Path(pfile).unlink(missing_ok=True)
    except Exception as e:  # environment: no commit, git failure
        return PatchCheck("skipped", f"worktree unavailable: {e}")
    if r.returncode == 0:
        return PatchCheck("applies", f"git apply --check OK at {commit}")
    return PatchCheck("fails", (r.stderr or r.stdout).strip())

"""Read ``yellowback::Params`` straight from ycash6 source at a git ref (owner: WP-0).

The parser understands exactly the C++ subset ``params.h`` / ``params.cpp`` use: the ``Params``
struct declarations, ``static const`` scalar constants, the constructor's initialiser list,
``SetCommon`` / ``MainParams`` / ``TestParams`` / ``RegtestParams`` bodies (assignments such as
``p.bondMin = 20000 * COIN;``, ``p.classMin[0] = 34560;  p.classMax[0] = 103680;``,
``r.bundleCarrier = bundleCarrier;``, ``m.addressVersion = { 0x1F, 0xE4 };``), and the
``RegtestParams`` default arguments. Anything else raises :class:`ExtractError` — loud failure is
the point, so a source change the parser does not understand is caught as drift.

Sources are read with ``git show <ref>:<path>``, never from the working tree.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ybcal.params.registry import (
    BUNDLE_CARRIERS,
    PARAMS_CPP,
    PARAMS_H,
    PINNED_COMMIT,
    REGTEST_FLAG_DEFAULTS,
    ParamSpec,
)
from ybcal.types import ParamValue
from ybcal.units import COIN

#: Snapshot of the extraction at the pin, shipped for environments without a ycash6 clone (CI).
SNAPSHOT_PATH: Path = Path(__file__).with_name(f"snapshot_{PINNED_COMMIT}.json")

#: Identifiers the C++ code takes from headers outside params.h.
_EXTERNAL_CONSTANTS: dict[str, int] = {"COIN": COIN}

#: RegtestParams argument name → Params field it sets.
_REGTEST_ARG_FIELD: dict[str, str] = {
    "startHeight": "startHeight",
    "sigmaRefBps": "sigmaRefBps",
    "supplyCapBps": "supplyCapBps",
    "enforceUntil": "enforceUntilHeight",
    "attestArmMin": "attestArmMin",
    "bundleCarrier": "bundleCarrier",
}


class ExtractError(RuntimeError):
    """The source holds a construct the parser does not understand (treated as drift)."""


# ---------------------------------------------------------------------------------------------------
# Source access


def git_show(repo: str | Path, ref: str, path: str) -> str:
    """``git -C repo show ref:path`` as text."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:{path}"],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError as e:  # pragma: no cover - git missing
        raise ExtractError("git is not installed") from e
    except subprocess.CalledProcessError as e:
        raise ExtractError(f"git show {ref}:{path} failed in {repo}: {e.stderr.strip()}") from e
    return out.stdout


def resolve_commit(repo: str | Path, ref: str) -> str:
    """Full commit hash of ``ref`` in ``repo``."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        raise ExtractError(f"cannot resolve {ref!r} in {repo}: {e.stderr.strip()}") from e
    return out.stdout.strip()


# ---------------------------------------------------------------------------------------------------
# Lexical helpers


def strip_comments(src: str) -> str:
    """Remove ``//`` and ``/* */`` comments, leaving string and char literals intact."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i : j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise ExtractError("unterminated block comment")
            out.append(" ")
            i = j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _match_brace(src: str, open_idx: int) -> int:
    """Index of the ``}`` matching the ``{`` at ``open_idx``."""
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ExtractError("unbalanced braces")


def _body_after(src: str, pattern: str) -> str:
    """The brace-delimited body that follows the first match of ``pattern``."""
    m = re.search(pattern, src)
    if not m:
        raise ExtractError(f"cannot find {pattern!r}")
    start = src.index("{", m.end() - 1 if src[m.end() - 1] == "{" else m.end())
    return src[start + 1 : _match_brace(src, start)]


def _split_statements(body: str) -> list[str]:
    """Split on top-level ``;`` (braces inside initialiser lists are kept together)."""
    stmts, depth, cur = [], 0, []
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == ";" and depth == 0:
            s = "".join(cur).strip()
            if s:
                stmts.append(s)
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        stmts.append(tail)
    return stmts


# ---------------------------------------------------------------------------------------------------
# Expression evaluation


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Div)


def _eval_int(node: ast.AST, env: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_int(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ExtractError(f"unknown identifier {node.id!r}")
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_int(node.operand, env)
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        a, b = _eval_int(node.left, env), _eval_int(node.right, env)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        # C++ integer division truncates toward zero
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q
    raise ExtractError(f"unsupported expression {ast.dump(node)}")


def eval_expr(expr: str, env: Mapping[str, Any]) -> ParamValue:
    """Evaluate one C++ right-hand side used in params.cpp/.h."""
    e = expr.strip()
    if e in ("true", "false"):
        return e == "true"
    m = re.fullmatch(r'"([^"]*)"', e)
    if m:
        return m.group(1)
    m = re.fullmatch(r"BundleCarrier::(\w+)", e)
    if m:
        if m.group(1) not in BUNDLE_CARRIERS:
            raise ExtractError(f"unknown BundleCarrier {m.group(1)}")
        return m.group(1)
    m = re.fullmatch(r"\{([^{}]*)\}", e)
    if m:
        items = [x.strip() for x in m.group(1).split(",") if x.strip()]
        return "".join(f"{int(x, 0):02X}" for x in items)
    # integer arithmetic: strip C++ literal suffixes and casts we know
    e = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)(?:[uU]?[lL]{0,2}|[lL]{1,2}[uU]?)\b", r"\1", e)
    e = re.sub(r"\((?:int|int64_t|CAmount|Cents|MicroUsd)\)", "", e)
    e = re.sub(r"\b0[xX]([0-9a-fA-F]+)\b", lambda mm: str(int(mm.group(1), 16)), e)
    try:
        tree = ast.parse(e, mode="eval")
    except SyntaxError as err:
        raise ExtractError(f"cannot parse expression {expr!r}") from err
    return _eval_int(tree, env)


# ---------------------------------------------------------------------------------------------------
# params.h


def parse_constants(header: str) -> dict[str, int]:
    """Every scalar ``static const <type> NAME = <expr>;`` in params.h (arrays skipped)."""
    src = strip_comments(header)
    env: dict[str, Any] = dict(_EXTERNAL_CONSTANTS)
    out: dict[str, int] = {}
    for m in re.finditer(r"static\s+const\s+[\w:]+(?:\s+[\w:]+)?\s+(\w+)\s*=\s*([^;{}]+);", src):
        name, expr = m.group(1), m.group(2)
        val = eval_expr(expr, env)
        if isinstance(val, bool) or not isinstance(val, int):
            continue
        out[name] = val
        env[name] = val
    return out


def _struct_body(header_nc: str) -> str:
    return _body_after(header_nc, r"\bstruct\s+Params\s*\{")


def _remove_nested_blocks(body: str) -> str:
    """Drop ``{ … }`` blocks (inline method bodies) so only declarations remain."""
    out, depth = [], 0
    for ch in body:
        if ch == "{":
            depth += 1
            if depth == 1:
                out.append(";")
            continue
        if ch == "}":
            depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def parse_struct_fields(header: str, constants: Mapping[str, int] | None = None) -> list[str]:
    """The ``Params`` data members in declaration order, arrays expanded (``classMin[0]`` …)."""
    nc = strip_comments(header)
    consts = dict(constants) if constants is not None else parse_constants(header)
    body = _remove_nested_blocks(_struct_body(nc))
    fields: list[str] = []
    for stmt in body.split(";"):
        s = " ".join(stmt.split())
        if not s or "(" in s or s.startswith(("using ", "typedef ", "static ", "friend ")):
            continue
        s = re.sub(r"\b(?:public|private|protected)\s*:\s*", "", s).strip()
        if not s:
            continue
        while re.search(r"<[^<>]*>", s):
            s = re.sub(r"<[^<>]*>", "", s)
        m = re.fullmatch(r"(?:const\s+)?(?:unsigned\s+|signed\s+)?[\w:]+\s+(.+)", s)
        if not m:
            raise ExtractError(f"cannot parse declaration {stmt.strip()!r}")
        for decl in m.group(1).split(","):
            d = decl.strip()
            am = re.fullmatch(r"(\w+)\s*\[\s*(\w+)\s*\]", d)
            if am:
                size = am.group(2)
                n = int(size) if size.isdigit() else consts.get(size)
                if n is None:
                    raise ExtractError(f"unknown array size {size!r}")
                fields.extend(f"{am.group(1)}[{i}]" for i in range(n))
            elif re.fullmatch(r"\w+", d):
                fields.append(d)
            else:
                raise ExtractError(f"cannot parse declarator {d!r}")
    return fields


def parse_regtest_defaults(header: str) -> dict[str, ParamValue]:
    """Default arguments of the ``RegtestParams`` declaration (``attestArmMin = 3`` …)."""
    nc = strip_comments(header)
    m = re.search(r"Params\s+RegtestParams\s*\(([^)]*)\)", nc)
    if not m:
        raise ExtractError("RegtestParams declaration not found")
    out: dict[str, ParamValue] = {}
    for arg in m.group(1).split(","):
        am = re.fullmatch(r"\s*[\w:]+\s+(\w+)\s*=\s*(.+?)\s*", arg)
        if am:
            out[am.group(1)] = eval_expr(am.group(2), {})
    return out


# ---------------------------------------------------------------------------------------------------
# params.cpp


def parse_constructor_defaults(cpp: str, fields: list[str]) -> dict[str, ParamValue]:
    """``Params::Params()`` initialiser list and body loop → default values for every field."""
    nc = strip_comments(cpp)
    m = re.search(r"Params::Params\s*\(\s*\)\s*:(.*?)\{", nc, re.S)
    if not m:
        raise ExtractError("Params::Params() not found")
    vals: dict[str, ParamValue] = {}
    for im in re.finditer(r"(\w+)\s*\(([^()]*)\)", m.group(1)):
        vals[im.group(1)] = eval_expr(im.group(2), {}) if im.group(2).strip() else 0
    body = _body_after(nc, r"Params::Params\s*\(\s*\)\s*:[^{]*\{")
    # the loop "classMin[i] = classMax[i] = baseRatioBps[i] = 0;" zeroes the arrays
    loop = re.search(r"for\s*\([^)]*\)\s*\{(.*?)\}", body, re.S)
    if loop:
        chain = loop.group(1).strip().rstrip(";")
        parts = [p.strip() for p in chain.split("=")]
        value = eval_expr(parts[-1], {})
        for p in parts[:-1]:
            am = re.fullmatch(r"(\w+)\[i\]", p)
            if not am:
                raise ExtractError(f"unexpected constructor loop statement {chain!r}")
            for f in fields:
                if f.startswith(am.group(1) + "["):
                    vals[f] = value
    out: dict[str, ParamValue] = {}
    for f in fields:
        if f in vals:
            out[f] = vals[f]
        elif f == "network" or f == "addressVersion":
            out[f] = ""
        else:
            raise ExtractError(f"Params() leaves {f!r} uninitialised")
    return out


def _apply_body(
    body: str,
    var: str,
    values: dict[str, ParamValue],
    env: Mapping[str, Any],
    functions: Mapping[str, str],
) -> None:
    """Execute the assignments of one function body onto ``values`` (in place)."""
    for stmt in _split_statements(body):
        s = " ".join(stmt.split())
        if re.fullmatch(rf"Params\s+{var}", s) or s.startswith("return "):
            continue
        cm = re.fullmatch(rf"(\w+)\s*\(\s*{var}\s*\)", s)
        if cm:
            if cm.group(1) not in functions:
                raise ExtractError(f"call to unknown helper {cm.group(1)}")
            _apply_body(functions[cm.group(1)], "p", values, env, functions)
            continue
        am = re.fullmatch(rf"{var}\.(\w+)(?:\s*\[\s*(\d+)\s*\])?\s*=\s*(.+)", s)
        if not am:
            raise ExtractError(f"unsupported statement {s!r}")
        name = am.group(1) + (f"[{am.group(2)}]" if am.group(2) is not None else "")
        if name not in values:
            raise ExtractError(f"assignment to unknown field {name!r}")
        values[name] = eval_expr(am.group(3), env)


def _lambda_body(nc: str, func: str) -> tuple[str, str]:
    """Body and variable of ``static Params p = [] { Params m; … }();`` inside ``func``."""
    outer = _body_after(nc, rf"const\s+Params\s*&\s*{func}\s*\(\s*\)\s*\{{")
    lm = re.search(r"\[\s*\]\s*\{", outer)
    if not lm:
        raise ExtractError(f"{func}: lambda initialiser not found")
    start = outer.index("{", lm.start())
    inner = outer[start + 1 : _match_brace(outer, start)]
    vm = re.search(r"\bParams\s+(\w+)\s*;", inner)
    if not vm:
        raise ExtractError(f"{func}: local Params not found")
    return inner, vm.group(1)


@dataclass(frozen=True)
class Extracted:
    """Everything read from one ref: field list, constants and one value dict per network."""

    ref: str
    commit: str
    fields: tuple[str, ...]
    constants: dict[str, int]
    networks: dict[str, dict[str, ParamValue]]   #: "main", "test", "regtest"
    regtest_flags: dict[str, ParamValue]

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form (the ``ybcal params extract`` output and the snapshot format)."""
        return {
            "format": "ybcal-extract/1",
            "ref": self.ref,
            "commit": self.commit,
            "fields": list(self.fields),
            "constants": dict(self.constants),
            "regtest_flags": dict(self.regtest_flags),
            "networks": {k: dict(v) for k, v in self.networks.items()},
        }

    def to_json(self) -> str:
        """Canonical JSON text (sorted network keys, field order preserved)."""
        return json.dumps(self.to_dict(), indent=2) + "\n"

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Extracted:
        """Inverse of :meth:`to_dict`."""
        if d.get("format") != "ybcal-extract/1":
            raise ExtractError("not a ybcal-extract/1 document")
        return cls(
            ref=d["ref"], commit=d["commit"], fields=tuple(d["fields"]), constants=dict(d["constants"]),
            networks={k: dict(v) for k, v in d["networks"].items()}, regtest_flags=dict(d["regtest_flags"]),
        )

    def values(self, network: str) -> dict[str, ParamValue]:
        """Field values of ``network`` merged with the header constants (registry key space)."""
        out: dict[str, ParamValue] = dict(self.networks[network])
        out.update(self.constants)
        return out

    def registry_values(self, network: str) -> dict[str, ParamValue]:
        """:meth:`values` plus every proposed field the source lacks, at the registry's value for a
        node without it (``regtest`` column on regtest, ``mainnet`` otherwise)."""
        from ybcal.params.registry import REGISTRY

        out = self.values(network)
        for name, spec in REGISTRY.items():
            if spec.proposed and name not in out:
                out[name] = spec.regtest if network == "regtest" else spec.mainnet
        return out


def extract_sources(
    header: str,
    cpp: str,
    *,
    ref: str = "",
    commit: str = "",
    regtest_flags: Mapping[str, ParamValue] | None = None,
) -> Extracted:
    """Parse already-read params.h / params.cpp text."""
    constants = parse_constants(header)
    fields = parse_struct_fields(header, constants)
    defaults = parse_constructor_defaults(cpp, fields)
    nc = strip_comments(cpp)
    functions = {"SetCommon": _body_after(nc, r"void\s+SetCommon\s*\(\s*Params\s*&\s*p\s*\)\s*\{")}
    env: dict[str, Any] = {**_EXTERNAL_CONSTANTS, **constants}

    networks: dict[str, dict[str, ParamValue]] = {}
    for net, func in (("main", "MainParams"), ("test", "TestParams")):
        body, var = _lambda_body(nc, func)
        vals = dict(defaults)
        _apply_body(body, var, vals, env, functions)
        networks[net] = vals

    # regtest: header defaults < registry flag defaults < caller overrides (keyed by arg or field name)
    hdr_defaults = parse_regtest_defaults(header)
    field_to_arg = {v: k for k, v in _REGTEST_ARG_FIELD.items()}
    flags_by_arg: dict[str, ParamValue] = dict(hdr_defaults)
    for fname, val in REGTEST_FLAG_DEFAULTS.items():
        flags_by_arg.setdefault(field_to_arg[fname], val)
    for k, val in (regtest_flags or {}).items():
        flags_by_arg[field_to_arg.get(k, k)] = val
    missing = set(_REGTEST_ARG_FIELD) - set(flags_by_arg)
    if missing:
        raise ExtractError(f"no value for RegtestParams argument(s) {sorted(missing)}")
    rbody = _body_after(nc, r"Params\s+RegtestParams\s*\([^)]*\)\s*\{")
    vals = dict(defaults)
    _apply_body(rbody, "r", vals, {**env, **flags_by_arg}, functions)
    networks["regtest"] = vals

    flags = {_REGTEST_ARG_FIELD[a]: flags_by_arg[a] for a in _REGTEST_ARG_FIELD}
    return Extracted(ref=ref, commit=commit, fields=tuple(fields), constants=constants,
                     networks=networks, regtest_flags=flags)


def extract(
    repo: str | Path, ref: str = PINNED_COMMIT, *, regtest_flags: Mapping[str, ParamValue] | None = None
) -> Extracted:
    """Extract the parameter sets from a ycash6 clone at ``ref`` (read via ``git show``)."""
    header = git_show(repo, ref, PARAMS_H)
    cpp = git_show(repo, ref, PARAMS_CPP)
    commit = resolve_commit(repo, ref)
    return extract_sources(header, cpp, ref=ref, commit=commit, regtest_flags=regtest_flags)


def load_snapshot(path: Path = SNAPSHOT_PATH) -> Extracted:
    """The committed extraction at :data:`PINNED_COMMIT` (fallback when ycash6 is absent)."""
    return Extracted.from_dict(json.loads(path.read_text()))


# ---------------------------------------------------------------------------------------------------
# Drift


@dataclass(frozen=True)
class Drift:
    """One difference between the registry and source."""

    #: "missing-from-registry" | "extra-in-registry" | "value-mismatch" | "missing-constant"
    kind: str
    name: str
    network: str = ""
    registry: ParamValue | None = None
    source: ParamValue | None = None

    def __str__(self) -> str:
        if self.kind == "value-mismatch":
            return (f"{self.kind}: {self.name} [{self.network}] "
                    f"registry={self.registry!r} source={self.source!r}")
        return f"{self.kind}: {self.name}"


def check_drift(registry: Mapping[str, ParamSpec], extracted: Extracted) -> list[Drift]:
    """Fields missing from the registry, extra registry entries, and value mismatches.

    Mainnet and regtest columns are compared (testnet differs from mainnet only in identity and
    release fields, which the registry does not carry).
    """
    out: list[Drift] = []
    fields = list(extracted.fields)
    out.extend(Drift("missing-from-registry", f) for f in fields if f not in registry)
    for name, spec in registry.items():
        if spec.origin == "field" and name not in fields and not spec.proposed:
            out.append(Drift("extra-in-registry", name))
        if spec.origin == "header" and name not in extracted.constants:
            out.append(Drift("missing-constant", name))
    for net, attr in (("main", "mainnet"), ("regtest", "regtest")):
        src = extracted.values(net)
        for name, spec in registry.items():
            if name not in src:
                continue
            want = getattr(spec, attr)
            got = src[name]
            if type(want) is not type(got) or want != got:
                out.append(Drift("value-mismatch", name, net, want, got))
    return out

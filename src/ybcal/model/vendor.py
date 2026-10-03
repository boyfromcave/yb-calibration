"""Vendoring of the ycash6 reference model (owner: WP-1; PLAN §2.1, §6.4).

The node repository carries an independent, standard-library-only Python implementation of
the Yellowback rules (``qa/rpc-tests/test_framework/yellowback_model.py``) and its v3 attestation
helpers (``yellowback_attest.py``). ``ybcal`` vendors them at the pinned commit so the kernels can be
tested byte for byte against them without a ycash6 clone:

- ``reference.py`` -- ``yellowback_model.py``, whole file + import-line rewrites;
- ``reference_attest.py`` -- ``yellowback_attest.py``, whole file + import-line rewrites;
- ``reference_util.py`` -- verbatim top-level definitions of ``yellowback_util.py`` and
  ``util.py`` (AST extract);
- ``yellowback_golden.json``, ``SERIALISATION.md`` -- verbatim (sha256 in ``VENDOR.json``);

all from ``qa/rpc-tests/test_framework/`` at the pin.

The only edits are the relative-import lines (the upstream package is ``test_framework``; here the
modules are renamed), listed in :data:`SPECS` and checked to match exactly on every re-vendor.
``yellowback_util.py`` imports the whole node test framework (``BitcoinTestFramework``, RPC
proxies), so only the top-level definitions ``yellowback_attest.py`` needs for the pure paths
(constants, the secp256k1 helpers, ``fee_zat`` …) are copied, each one verbatim, with the
transitive closure of the names they use. Node drivers are *not* vendored: calling one through
``reference_attest`` raises ``AttributeError``/``ImportError``, which is the intended failure.

Every Python file starts with a pin header (source path, commit, sha256 of the original, sha256 of
the body as written). :func:`check_vendored` re-derives both hashes, so an accidental edit of a
vendored file fails ``ybcal verify`` and the test suite. :func:`revendor` regenerates everything
from a ycash6 clone at a given ref (``ybcal verify --revendor --ycash6 PATH --ref REF``).
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

OWNER_WP = "WP-1"

#: The commit the shipped files were vendored from (full hash of the pin ``7702d22``).
VENDORED_COMMIT = "7702d22606d1ec3edf0fca962573fba64dac3904"
SOURCE_REPO = "boyfromcave/ycash6"
TF = "qa/rpc-tests/test_framework/"
MODEL_DIR = Path(__file__).resolve().parent
MANIFEST = "VENDOR.json"

HEADER_BEGIN = "# ==== ybcal vendored file -- do not edit; re-vendor with `ybcal verify --revendor` ===="
HEADER_END = "# ==== end of ybcal header; the vendored source follows unchanged ===="


@dataclass(frozen=True)
class WholeSpec:
    """A whole upstream file, copied with exact line rewrites (each must match ``count`` times)."""

    dest: str
    source: str
    rewrites: tuple[tuple[str, str, int], ...] = ()


@dataclass(frozen=True)
class ExtractSpec:
    """Verbatim top-level definitions (and their transitive closure) from upstream modules."""

    dest: str
    #: (source path, root names) in resolution order; names a module imports from a later
    #: source (``from .util import x``) are resolved there.
    sources: tuple[tuple[str, tuple[str, ...]], ...]
    #: relative import of the model package (``from . import yellowback_model as ym``) → local
    module_aliases: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DataSpec:
    dest: str
    source: str


_ATTEST_IMPORT = ("        from . import yellowback_attest as ya",
                  "        from . import reference_attest as ya")

SPECS: tuple[WholeSpec | ExtractSpec | DataSpec, ...] = (
    WholeSpec("reference.py", TF + "yellowback_model.py", ((*_ATTEST_IMPORT, 8),)),
    WholeSpec("reference_attest.py", TF + "yellowback_attest.py", (
        ("from .util import assert_equal, bytes_to_hex_str, hex_str_to_bytes",
         "from .reference_util import assert_equal, bytes_to_hex_str, hex_str_to_bytes", 1),
        ("from . import yellowback_model as ym", "from . import reference as ym", 1),
        ("from . import yellowback_util as yu", "from . import reference_util as yu", 1),
        ("from .yellowback_util import (", "from .reference_util import (", 1),
    )),
    ExtractSpec(
        "reference_util.py",
        (
            (TF + "yellowback_util.py", (
                # the names yellowback_attest.py imports from yellowback_util at module level
                "ATTEST_ARM_DELAY", "ATTEST_ARM_MIN", "BOND_MATURITY", "BOND_MIN_LOCK", "BOND_MIN_ZAT",
                "BUNDLE_MAX", "CARRIER_VALUE", "K_SLACK", "M_SELECT", "N_SLOTS", "Q_HIGH_BPS", "Q_LOW_BPS",
                "REF_LAG", "REF_WINDOW", "SIGNING_BRANCH_ID", "TOKEN_VALUE", "YELLOWBACK_FEE", "ATTESTOR_A",
                "ATTESTOR_B", "USER", "POOLS", "FEE_VOUT_NONE", "GRACE", "PAYLOAD_VERSION_V3", "AGE_CAP",
                "FOUNDING_WINDOW",
                # the pure helpers it reaches through ``yu.`` (node drivers deliberately omitted)
                "_SECP", "_ec_add", "secret_to_pubkey", "wif_to_secret", "pubkey_to_address", "fee_zat",
                "term_class_of", "usd_to_micro", "ATTEST_FEE_BPS", "BPS",
            )),
            (TF + "util.py", ("assert_equal", "bytes_to_hex_str", "hex_str_to_bytes")),
        ),
        {"yellowback_model": "reference"},
    ),
    DataSpec("yellowback_golden.json", TF + "yellowback_golden.json"),
    DataSpec("SERIALISATION.md", TF + "SERIALISATION.md"),
)


# ---------------------------------------------------------------------------------------------------
# helpers


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_show(repo: Path | str, ref: str, path: str) -> bytes:
    """``git -C repo show ref:path`` (read-only)."""
    return subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"],
                          check=True, capture_output=True).stdout


def git_commit(repo: Path | str, ref: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", f"{ref}^{{commit}}"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _header(lines: Iterable[str]) -> str:
    return "\n".join([HEADER_BEGIN, *(f"# {ln}" for ln in lines), "# ruff: noqa", HEADER_END]) + "\n"


def split_header(text: str) -> tuple[dict[str, str], str]:
    """(header fields, body) of a vendored Python file; raises ValueError if there is no header."""
    if not text.startswith(HEADER_BEGIN + "\n"):
        raise ValueError("missing ybcal vendor header")
    end = text.index(HEADER_END + "\n")
    fields: dict[str, str] = {}
    for ln in text[len(HEADER_BEGIN) + 1:end].splitlines():
        ln = ln.removeprefix("# ")
        if ":" in ln:
            k, v = ln.split(":", 1)
            fields.setdefault(k.strip(), v.strip())
    return fields, text[end + len(HEADER_END) + 1:]


def apply_rewrites(text: str, rewrites: Iterable[tuple[str, str, int]]) -> str:
    """Replace whole lines exactly; the number of matches must equal the declared count."""
    lines = text.split("\n")
    for old, new, count in rewrites:
        hits = [i for i, ln in enumerate(lines) if ln == old]
        if len(hits) != count:
            raise ValueError(f"rewrite {old!r}: expected {count} line(s), found {len(hits)} "
                             "(upstream changed: update ybcal.model.vendor.SPECS)")
        for i in hits:
            lines[i] = new
    return "\n".join(lines)


def undo_rewrites(text: str, rewrites: Iterable[tuple[str, str, int]]) -> str:
    return apply_rewrites(text, [(new, old, count) for old, new, count in rewrites])


# ---------------------------------------------------------------------------------------------------
# AST extraction (reference_util.py)


def _top_level(tree: ast.Module) -> tuple[dict[str, ast.stmt], dict[str, ast.stmt]]:
    """(name → defining statement, name → import statement) for module-level statements."""
    defs: dict[str, ast.stmt] = {}
    imports: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            defs[node.name] = node
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        defs[n.id] = node
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for a in node.names:
                imports[a.asname or a.name.split(".")[0]] = node
    return defs, imports


def _used_names(node: ast.stmt) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def extract_source(spec: ExtractSpec, sources: dict[str, str]) -> str:
    """The extracted module body: needed imports, then the definitions in source order."""
    parsed = {path: ast.parse(text) for path, text in sources.items()}
    tables = {path: _top_level(tree) for path, tree in parsed.items()}
    order = [path for path, _ in spec.sources]
    wanted: dict[str, set[ast.stmt]] = {p: set() for p in order}
    import_lines: list[str] = []
    pending = [(order[0], name) for name in spec.sources[0][1]]
    for path, roots in spec.sources[1:]:
        pending += [(path, name) for name in roots]
    seen: set[tuple[str, str]] = set()
    while pending:
        path, name = pending.pop()
        if (path, name) in seen:
            continue
        seen.add((path, name))
        defs, imports = tables[path]
        if name in defs:
            stmt = defs[name]
            if stmt not in wanted[path]:
                wanted[path].add(stmt)
                pending += [(path, n) for n in _used_names(stmt) if n != name]
            continue
        if name not in imports:
            continue    # a builtin, a local or a parameter
        imp = imports[name]
        if isinstance(imp, ast.ImportFrom) and imp.level == 1:
            mod = imp.module or ""
            if mod == "":   # from . import <module> as <name>
                alias = next(a for a in imp.names if (a.asname or a.name) == name)
                local = spec.module_aliases.get(alias.name)
                if local is None:
                    raise ValueError(f"{path}: needs relative module {alias.name!r} (not vendored)")
                line = f"from . import {local} as {name}"
            else:
                later = [p for p in order if p.endswith("/" + mod + ".py")]
                if not later:
                    raise ValueError(f"{path}: needs {name!r} from .{mod} (not vendored)")
                pending.append((later[0], name))
                continue
        else:
            if isinstance(imp, ast.ImportFrom):
                alias = next(a for a in imp.names if (a.asname or a.name) == name)
                suffix = f" as {alias.asname}" if alias.asname else ""
                line = f"from {imp.module} import {alias.name}{suffix}"
            else:
                alias = next(a for a in imp.names if (a.asname or a.name.split('.')[0]) == name)
                line = f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")
        if line not in import_lines:
            import_lines.append(line)
    out = ['"""Verbatim top-level definitions extracted from the ycash6 test framework (see header)."""', ""]
    out += sorted(import_lines)
    for path in order:
        stmts = sorted(wanted[path], key=lambda s: s.lineno)
        if not stmts:
            continue
        out += ["", "", f"# ---- from {path}"]
        text = sources[path]
        lines = text.splitlines()
        for stmt in stmts:
            start = stmt.lineno - 1
            if getattr(stmt, "decorator_list", None):
                start = min(d.lineno for d in stmt.decorator_list) - 1  # type: ignore[attr-defined]
            seg = "\n".join(lines[start:stmt.end_lineno])
            out += ["", seg]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------------
# (re)vendoring and integrity


def render(spec: WholeSpec | ExtractSpec | DataSpec, fetch, commit: str) -> bytes:
    """The bytes of one vendored file, with ``fetch(path) -> bytes`` reading the source at ``commit``."""
    if isinstance(spec, DataSpec):
        return fetch(spec.source)
    if isinstance(spec, WholeSpec):
        raw = fetch(spec.source)
        body = apply_rewrites(raw.decode("utf-8"), spec.rewrites)
        rw = "; ".join(f"{o.strip()!r} -> {n.strip()!r} (x{c})" for o, n, c in spec.rewrites) or "none"
        head = _header([
            f"source: {SOURCE_REPO} {spec.source}",
            f"commit: {commit}",
            f"sha256: {sha256_hex(raw)}",
            f"body-sha256: {sha256_hex(body.encode('utf-8'))}",
            "mode: whole file, unmodified except these import-line rewrites (vendor.SPECS):",
            f"rewrites: {rw}",
        ])
        return (head + body).encode("utf-8")
    sources = {p: fetch(p).decode("utf-8") for p, _ in spec.sources}
    body = extract_source(spec, sources)
    head = _header([
        f"source: {SOURCE_REPO} " + " + ".join(p for p, _ in spec.sources),
        f"commit: {commit}",
        "sha256: " + " ".join(sha256_hex(sources[p].encode("utf-8")) for p, _ in spec.sources),
        f"body-sha256: {sha256_hex(body.encode('utf-8'))}",
        "mode: verbatim top-level definitions (transitive closure of the roots in vendor.SPECS)",
    ])
    return (head + body).encode("utf-8")


def revendor(ycash6: Path | str, ref: str, dest: Path | str = MODEL_DIR) -> dict[str, str]:
    """Regenerate every vendored file from ``ycash6`` at ``ref`` into ``dest``.

    Returns ``{file: sha256 of what was written}``. Never writes to the ycash6 clone. After a
    re-pin, also update ``VENDORED_COMMIT`` (and the registry pin; see docs/decisions.md).
    """
    commit = git_commit(ycash6, ref)
    dest = Path(dest)
    out: dict[str, str] = {}
    sources: dict[str, str] = {}
    for spec in SPECS:
        data = render(spec, lambda p: git_show(ycash6, commit, p), commit)
        (dest / spec.dest).write_bytes(data)
        out[spec.dest] = sha256_hex(data)
        if isinstance(spec, DataSpec | WholeSpec):
            sources[spec.dest] = spec.source
    manifest = {"repo": SOURCE_REPO, "commit": commit,
                "files": {k: {"sha256": v, "source": sources.get(k, "extract")} for k, v in out.items()}}
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return out


def check_vendored(dest: Path | str = MODEL_DIR) -> list[str]:
    """Problems with the vendored files (empty list = intact): header present, body hash, original
    hash for whole files (rewrites undone), data-file hashes against VENDOR.json, one commit."""
    dest = Path(dest)
    problems: list[str] = []
    try:
        manifest = json.loads((dest / MANIFEST).read_text())
    except (OSError, ValueError) as e:
        return [f"{MANIFEST}: {e}"]
    commit = manifest.get("commit")
    for spec in SPECS:
        path = dest / spec.dest
        try:
            data = path.read_bytes()
        except OSError as e:
            problems.append(f"{spec.dest}: {e}")
            continue
        want = manifest.get("files", {}).get(spec.dest, {}).get("sha256")
        if sha256_hex(data) != want:
            problems.append(f"{spec.dest}: sha256 differs from {MANIFEST} (edited?)")
        if isinstance(spec, DataSpec):
            continue
        try:
            fields, body = split_header(data.decode("utf-8"))
        except ValueError as e:
            problems.append(f"{spec.dest}: {e}")
            continue
        if fields.get("commit") != commit:
            problems.append(f"{spec.dest}: header commit {fields.get('commit')} != {commit}")
        if sha256_hex(body.encode("utf-8")) != fields.get("body-sha256"):
            problems.append(f"{spec.dest}: body edited (body-sha256 mismatch)")
        if isinstance(spec, WholeSpec):
            try:
                orig = undo_rewrites(body, spec.rewrites)
            except ValueError as e:
                problems.append(f"{spec.dest}: {e}")
                continue
            if sha256_hex(orig.encode("utf-8")) != fields.get("sha256"):
                problems.append(f"{spec.dest}: body differs from upstream beyond the import rewrites")
    return problems


def check_against_source(ycash6: Path | str, ref: str | None = None,
                         dest: Path | str = MODEL_DIR) -> list[str]:
    """Problems when the vendored files differ from a fresh render from ``ycash6`` (default: the
    vendored commit). Read-only towards ycash6."""
    commit = git_commit(ycash6, ref or VENDORED_COMMIT)
    problems = []
    for spec in SPECS:
        fresh = render(spec, lambda p: git_show(ycash6, commit, p), commit)
        if (Path(dest) / spec.dest).read_bytes() != fresh:
            problems.append(f"{spec.dest}: differs from {commit[:12]}:{getattr(spec, 'source', 'extract')}")
    return problems

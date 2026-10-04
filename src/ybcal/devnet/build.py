"""ycashd binaries for the devnet: build per overlay, fetch a CI artifact, or use an existing one.

Owner: WP-9.

Three ways to a binary, all ending in ``.work/bin/<key>/{ycashd,ycash-cli}`` plus a
``manifest.json`` recording where it came from:

1. **Build** (PLAN §6.2): a detached worktree at the pin (:mod:`ybcal.devnet.worktree`), the
   overlay patch applied to ``RegtestParams()`` there, ``./zcutil/build.sh -j N`` once (depends +
   configure + make, 35–60 min cold, needs the depends download hosts), then incremental
   ``make -C src -j N ycashd ycash-cli`` (~2 min). Cached by :func:`ybcal.devnet.overlay.build_key`
   (commit + compiled values; runtime flags need no rebuild). :func:`preflight` decides first
   whether a build is feasible and returns :class:`Skipped` when it is not.
2. **CI artifact** (:func:`fetch_ci_binary`): ``gh run download <run> -R boyfromcave/ycash6 -n
   release-<platform>`` (fallback ``gh api …/artifacts/<id>/zip``) into ``.work/bin/ci-<sha12>/``.
3. **Existing binary**: ``--ycashd PATH`` or ``$YBCAL_YCASHD``.

A binary must match the pin. :func:`binary_version` reads ``ycashd -version``; :func:`check_skew`
compares the binary's commit with the pin in the local clone and lists the parameter values that
differ between them (both networks) and the commits in between; :func:`compare_node_params`
compares a running node's ``yed_getinfo.params`` with the expected regtest set. A binary that
predates (or postdates) the pin is refused unless the caller passes ``allow_version_skew``.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ybcal.devnet.overlay import CARRIER_FLAG_VALUES, OverlaySplit, apply_patch, build_key, make_patch
from ybcal.devnet.status import Skipped
from ybcal.devnet.worktree import (
    Worktree,
    WorktreeError,
    create_worktree,
    is_ancestor,
    resolve_commit,
    work_dir,
    worktree_path,
)
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import PARAMS_CPP, PINNED_COMMIT
from ybcal.types import ParamValue

OWNER_WP = "WP-9"

#: GitHub repository the CI artifacts come from.
CI_REPO = "boyfromcave/ycash6"
#: The CI run the owner has (built from 94bafa4, eight commits before the pin).
KNOWN_CI_RUN = "37081639884"
KNOWN_CI_COMMIT = "94bafa4"

#: Hosts ``depends/`` downloads from at the pin (``depends/packages/*.mk``,
#: ``FALLBACK_DOWNLOAD_PATH``). A cold build needs all of them.
DEPENDS_HOSTS: tuple[str, ...] = (
    "https://archives.boost.io/",
    "https://download.z.cash/",
    "https://static.rust-lang.org/",
    "https://github.com/",
    "https://download.libsodium.org/",
    "https://download.oracle.com/",
)

#: Tools ``zcutil/build.sh`` needs (alternatives separated by ``|``).
REQUIRED_TOOLS: tuple[str, ...] = (
    "make",
    "git",
    "autoconf",
    "automake",
    "libtoolize|glibtoolize",
    "pkg-config",
    "curl",
    "m4",
    "python3",
    "g++|clang++",
)

#: ``ycashd -version`` / ``--version`` banner commit marker.
_BANNER_RE = re.compile(r"version\s+v?(?P<rest>\S+)", re.I)

Runner = Callable[..., subprocess.CompletedProcess[Any]]


# ---------------------------------------------------------------------------------------------------
# Binaries


@dataclass(frozen=True)
class BinaryInfo:
    """A usable ycashd (and ycash-cli when present)."""

    ycashd: Path
    ycash_cli: Path | None
    origin: Literal["build", "ci", "path", "env", "cache"]
    commit: str | None = None  #: the commit it was built from, when known
    key: str = ""  #: cache key (``.work/bin/<key>``)
    manifest: dict[str, Any] = field(default_factory=dict)


def bin_dir(key: str, base: Path | None = None) -> Path:
    """``.work/bin/<key>``."""
    return (base or work_dir()) / "bin" / key


def _manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads((path / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}


def cached_binary(key: str, base: Path | None = None) -> BinaryInfo | None:
    """The cached binary for ``key``, if ``ycashd`` is there and executable."""
    d = bin_dir(key, base)
    exe = d / "ycashd"
    if exe.is_file() and os.access(exe, os.X_OK):
        cli = d / "ycash-cli"
        m = _manifest(d)
        return BinaryInfo(exe, cli if cli.exists() else None, "cache", m.get("commit"), key, m)
    return None


def resolve_binary(
    ycashd: str | Path | None = None, *, key: str | None = None, base: Path | None = None
) -> BinaryInfo | Skipped:
    """``ycashd`` → ``$YBCAL_YCASHD`` → the cache entry ``key``; :class:`Skipped` when none exists."""
    for origin, cand in (("path", ycashd), ("env", os.environ.get("YBCAL_YCASHD"))):
        if not cand:
            continue
        p = Path(cand).expanduser().resolve()
        if not (p.is_file() and os.access(p, os.X_OK)):
            return Skipped(f"ycashd {p} ({origin}) is not an executable file", "binary")
        cli = p.with_name("ycash-cli")
        m = _manifest(p.parent)
        return BinaryInfo(p, cli if cli.exists() else None, origin, m.get("commit"), m.get("key", ""), m)  # type: ignore[arg-type]
    if key:
        hit = cached_binary(key, base)
        if hit:
            return hit
    return Skipped(
        "no ycashd binary: pass --ycashd PATH or set YBCAL_YCASHD, fetch a CI artifact "
        "(`ybcal devnet build --from-ci-run RUN`), or build one (`ybcal devnet build`)",
        "binary",
    )


# ---------------------------------------------------------------------------------------------------
# Version and skew


@dataclass(frozen=True)
class BinaryVersion:
    """Parsed ``ycashd -version`` banner."""

    banner: str
    version: str
    commit: str | None
    dirty: bool


def parse_version_banner(text: str) -> BinaryVersion:
    """Parse e.g. ``Ycash Daemon version v6.21.0-rc1-94bafa4-dirty``.

    The commit is the last ``-``-separated token that is 7–40 hex digits (``g`` prefix allowed);
    a tagged build (``BUILD_DESC`` from ``git describe``) carries none.
    """
    first = text.strip().splitlines()[0] if text.strip() else ""
    m = _BANNER_RE.search(first)
    if not m:
        return BinaryVersion(first, "", None, False)
    tokens = m.group("rest").split("-")
    dirty = bool(tokens) and tokens[-1] == "dirty"
    if dirty:
        tokens = tokens[:-1]
    commit = None
    if len(tokens) > 1 and re.fullmatch(r"g?[0-9a-f]{7,40}", tokens[-1]):
        commit = (
            tokens[-1].removeprefix("g") if not re.fullmatch(r"[0-9a-f]{7,40}", tokens[-1]) else tokens[-1]
        )
        tokens = tokens[:-1]
    return BinaryVersion(first, "-".join(tokens), commit, dirty)


def binary_version(ycashd: str | Path, *, run: Runner = subprocess.run, timeout: float = 30) -> BinaryVersion:
    """Run ``ycashd -version`` and parse the first line."""
    proc = run([str(ycashd), "-version"], capture_output=True, text=True, timeout=timeout)
    return parse_version_banner((proc.stdout or "") + (proc.stderr or ""))


@dataclass
class SkewReport:
    """How a binary's commit relates to the pin, and what differs between them."""

    binary_commit: str | None
    pin: str
    relation: Literal["equal", "predates", "postdates", "diverged", "unknown"]
    commits_between: list[str] = field(default_factory=list)
    param_diffs: dict[str, dict[str, tuple[ParamValue, ParamValue]]] = field(default_factory=dict)
    allowed: bool = False
    message: str = ""

    @property
    def ok(self) -> bool:
        """True when the binary matches the pin, or skew was explicitly allowed."""
        return self.relation == "equal" or self.allowed

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "binary_commit": self.binary_commit,
            "pin": self.pin,
            "relation": self.relation,
            "commits_between": self.commits_between,
            "allowed": self.allowed,
            "message": self.message,
            "param_diffs": {n: {k: list(v) for k, v in d.items()} for n, d in self.param_diffs.items()},
        }


def _param_diffs(repo: Path, a: str, b: str) -> dict[str, dict[str, tuple[ParamValue, ParamValue]]]:
    from ybcal.params.extract import extract

    ea, eb = extract(repo, a), extract(repo, b)
    out = {}
    for net in ("regtest", "main"):
        pa, pb = ParamSet.from_extracted(ea, net), ParamSet.from_extracted(eb, net)
        out[net] = pa.diff(pb)
    return out


def check_skew(
    repo: str | Path | None, binary_commit: str | None, pin: str = PINNED_COMMIT, *, allow: bool = False
) -> SkewReport:
    """Compare ``binary_commit`` with ``pin`` in the local clone ``repo``.

    ``param_diffs[network]`` maps each parameter that differs to ``(binary value, pin value)``.
    """
    if not binary_commit:
        return SkewReport(
            None,
            pin,
            "unknown",
            allowed=allow,
            message="the binary's commit is unknown (tagged banner, no manifest); "
            "its yed_getinfo.params will be compared once a node is up",
        )
    if repo is None:
        rel = "equal" if pin.startswith(binary_commit) or binary_commit.startswith(pin) else "unknown"
        return SkewReport(
            binary_commit,
            pin,
            rel,
            allowed=allow,
            message="" if rel == "equal" else "no ycash6 clone to relate the commits",
        )
    repo = Path(repo)
    try:
        bc, pc = resolve_commit(repo, binary_commit), resolve_commit(repo, pin)
    except WorktreeError as e:
        return SkewReport(binary_commit, pin, "unknown", allowed=allow, message=str(e))
    if bc == pc:
        return SkewReport(bc[:12], pin, "equal", allowed=allow)
    if is_ancestor(repo, bc, pc):
        relation: Literal["predates", "postdates", "diverged"] = "predates"
        rng = f"{bc}..{pc}"
    elif is_ancestor(repo, pc, bc):
        relation, rng = "postdates", f"{pc}..{bc}"
    else:
        relation, rng = "diverged", f"{bc}...{pc}"
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline", "--no-decorate", rng], capture_output=True, text=True
    ).stdout.splitlines()
    try:
        diffs = _param_diffs(repo, bc, pc)
    except Exception as e:  # pragma: no cover - parser drift at an old commit
        diffs = {}
        log.append(f"(parameter extraction failed at {bc[:12]}: {e})")
    n_reg, n_main = len(diffs.get("regtest", {})), len(diffs.get("main", {}))
    msg = (
        f"binary {bc[:12]} {relation} the pin {pin} by {len(log)} commit(s); "
        f"{n_reg} regtest / {n_main} mainnet parameter value(s) differ; rule changes in those commits are "
        "not visible in parameters"
    )
    return SkewReport(bc[:12], pin, relation, log, diffs, allow, msg)


#: ``yed_getinfo.params`` path → registry name (doc/yellowback-rpc.md, yed_getinfo).
GETINFO_PARAMS: dict[str, str] = {
    "startHeight": "startHeight",
    "enforceUntilHeight": "enforceUntilHeight",
    "sigmaRefBps": "sigmaRefBps",
    "supplyCapBps": "supplyCapBps",
    "refWindow": "refWindow",
    "grace": "grace",
    "payeeWindow": "payeeWindow",
    "feeMinZat": "feeMin",
    "feeBps": "feeBps",
    "tokenValueZat": "tokenValue",
    "valveBlocks": "valveBlocks",
    "abandonBlocks": "abandonBlocks",
    "windows.fast": "pFastWindow",
    "windows.mid": "pMidWindow",
    "windows.slow": "pSlowWindow",
    "windows.signal": "signalWindow",
    "minFill.fast": "pFastMinFill",
    "minFill.mid": "pMidMinFill",
    "minFill.slow": "pSlowMinFill",
    "globalRatioHaltBps": "globalRatioHaltBps",
    "recapRatioBps": "recapRatioBps",
    "policy.penaltyBlocks": "nPenalty",
    "policy.accuracyWindow": "accuracyWindow",
    "policy.tiltBps": "payeeTiltBps",
    "attest.armMin": "attestArmMin",
    "attest.armDelay": "attestArmDelay",
    "attest.required": "attestRequired",
    "attest.carrierMode": "bundleCarrier",
    "attest.nSlots": "nSlots",
    "attest.mSelect": "mSelect",
    "attest.kSlack": "kSlack",
    "attest.bundleMax": "bundleMax",
    "attest.qLowBps": "qLowBps",
    "attest.qHighBps": "qHighBps",
    "attest.attestMaxAge": "attestMaxAge",
    "attest.pinWindow": "pinWindow",
    "attest.pinDeltaBps": "pinDeltaBps",
    "attest.pinMinTags": "pinMinTags",
    "attest.pinMinBundles": "pinMinBundles",
    "attest.divergeBpsAttest": "divergeBpsAttest",
    "attest.emergencyRatioBps": "emergencyRatioBps",
    "attest.emergencyPersist": "emergencyPersist",
    "attest.emergencyNoticeTtl": "emergencyNoticeTtl",
    "attest.residualMinZat": "residualMinZat",
    "attest.attestFeeBps": "attestFeeBps",
    "attest.bondMinZat": "bondMin",
    "attest.bondMinLock": "bondMinLock",
    "attest.bondMaturity": "bondMaturity",
    "attest.ageCap": "ageCap",
    "attest.foundingWindow": "foundingWindow",
    "attest.dormancyBlocks": "dormancyBlocks",
    "attest.dormancyMinBundles": "dormancyMinBundles",
    "attest.dormancyCheck": "dormancyCheck",
    "attest.carrierValueZat": "carrierValue",
}

#: ``yed_getactivation`` key → registry name.
GETACTIVATION_PARAMS: dict[str, str] = {
    "window": "signalWindow",
    "threshold": "activationThreshold",
    "participationFloor": "participationFloor",
    "enforcementFloor": "enforcementFloor",
    "enforcementResume": "enforcementResume",
}

#: Wallet-policy values a node flag may override; a difference is reported, never fatal.
NODE_OVERRIDABLE: frozenset[str] = frozenset({"nPenalty", "accuracyWindow", "payeeTiltBps"})


def _dig(doc: Mapping[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return KeyError
        cur = cur[part]
    return cur


def compare_node_params(
    params: Mapping[str, Any], expected: Mapping[str, ParamValue], activation: Mapping[str, Any] | None = None
) -> dict[str, tuple[Any, ParamValue]]:
    """Registry name → ``(node value, expected value)`` for every reported value that differs.

    ``params`` is ``yed_getinfo()["params"]``; ``activation`` (optional) is ``yed_getactivation()``.
    A key missing from the node's answer is reported as ``(None, expected)``. The three classes
    (``params.classes``) are compared field by field.
    """
    out: dict[str, tuple[Any, ParamValue]] = {}

    def cmp(name: str, got: Any) -> None:
        want = expected[name]
        if name == "bundleCarrier" and isinstance(got, str):
            got = {v: k for k, v in CARRIER_FLAG_VALUES.items()}.get(got, got)
        if got is KeyError:
            out[name] = (None, want)
        elif got != want:
            out[name] = (got, want)

    for path, name in GETINFO_PARAMS.items():
        cmp(name, _dig(params, path))
    classes = params.get("classes")
    if isinstance(classes, list):
        for i, row in enumerate(classes[:3]):
            cmp(f"classMin[{i}]", row.get("minBlocks", KeyError))
            cmp(f"classMax[{i}]", row.get("maxBlocks", KeyError))
            cmp(f"baseRatioBps[{i}]", row.get("baseRatioBps", KeyError))
    if activation is not None:
        for key, name in GETACTIVATION_PARAMS.items():
            cmp(name, activation.get(key, KeyError))
    return out


@dataclass
class NodeCheck:
    """A running node's parameters against the expected regtest set."""

    differences: dict[str, tuple[Any, ParamValue]]
    allowed: bool

    @property
    def fatal(self) -> dict[str, tuple[Any, ParamValue]]:
        """Differences outside the node-overridable wallet policy."""
        return {k: v for k, v in self.differences.items() if k not in NODE_OVERRIDABLE}

    @property
    def ok(self) -> bool:
        """True when nothing fatal differs, or skew was allowed."""
        return not self.fatal or self.allowed


def check_node_params(client: Any, expected: ParamSet, *, allow: bool = False) -> NodeCheck:
    """``yed_getinfo`` / ``yed_getactivation`` of a running node against ``expected``."""
    info = client.call("yed_getinfo")
    act = client.call("yed_getactivation")
    return NodeCheck(compare_node_params(info.get("params", {}), expected, act), allow)


# ---------------------------------------------------------------------------------------------------
# Preflight


@dataclass(frozen=True)
class Ready:
    """Preflight passed: what was found."""

    tools: dict[str, str]
    hosts: dict[str, bool]
    depends_built: bool


def probe_host(url: str, timeout: float = 6.0) -> bool:
    """True when an HTTPS ``HEAD`` reaches the host.

    Any HTTP answer from the host counts as reachable; a refused proxy ``CONNECT`` (raised as a
    ``URLError``), DNS failure or timeout does not.
    """
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def depends_built(worktree: Path | None) -> bool:
    """True when ``depends/`` already holds a built host prefix (``<triplet>/share/config.site``)."""
    if worktree is None:
        return False
    return any((worktree / "depends").glob("*-*/share/config.site"))


def preflight(
    worktree: Path | None = None,
    *,
    probe: Callable[[str], bool] = probe_host,
    which: Callable[[str], str | None] = shutil.which,
    hosts: Sequence[str] = DEPENDS_HOSTS,
    min_free_gb: float = 8.0,
) -> Ready | Skipped:
    """Whether a build can run here; :class:`Skipped` naming every missing piece otherwise.

    Checks the toolchain (:data:`REQUIRED_TOOLS`), free disk under the work dir, and — unless
    ``depends/`` is already built in ``worktree`` — reachability of every depends download host.
    """
    reasons: list[str] = []
    tools: dict[str, str] = {}
    for alts in REQUIRED_TOOLS:
        found = next((p for a in alts.split("|") if (p := which(a))), None)
        if found:
            tools[alts] = found
        else:
            reasons.append(f"missing tool {alts.replace('|', ' or ')}")
    try:
        root = work_dir()
        probe_dir = root if root.exists() else root.parent
        free = shutil.disk_usage(probe_dir).free / 2**30
        if free < min_free_gb:
            reasons.append(
                f"only {free:.1f} GiB free under {probe_dir} (a depends build needs ~{min_free_gb:g})"
            )
    except OSError:
        pass
    built = depends_built(worktree)
    reach: dict[str, bool] = {}
    if not built:
        for h in hosts:
            reach[h] = probe(h)
        blocked = [h for h, ok in reach.items() if not ok]
        if blocked:
            reasons.append("depends download host(s) unreachable: " + ", ".join(blocked))
    if reasons:
        return Skipped("cannot build ycashd here: " + "; ".join(reasons), "build")
    return Ready(tools, reach, built)


# ---------------------------------------------------------------------------------------------------
# Build


@dataclass(frozen=True)
class BuildStep:
    """One command of a build."""

    argv: tuple[str, ...]
    cwd: Path
    what: str
    shell: bool = False


#: 6.20.0 needs the cxx bridge headers before any target-only make (doc/yellowback-devnet.md §0).
CXXBRIDGE_CMD = (
    "if grep -q '^CXXBRIDGE_H = ' src/Makefile.am; then "  # v4.5.0 (ycash-dd) has no cxx bridge
    "awk '/^CXXBRIDGE_H = /{f=1;next} f&&/^ *rust\\/gen/{gsub(/[ \\\\]/,\"\");print;next} f{exit}' "
    "src/Makefile.am | xargs make -C src -j{jobs}; fi"
)


def built_triples(tree: Path) -> list[str]:
    """Host triples whose ``depends/<triple>/share/config.site`` exists in ``tree`` (a built depends)."""
    d = tree / "depends"
    return sorted(p.parent.parent.name for p in d.glob("*-*/share/config.site")) if d.is_dir() else []


@dataclass(frozen=True)
class Reuse:
    """A built ycash6 tree whose ``depends/<triple>`` and cargo ``target/`` a worktree borrows.

    The depends prefix is symlinked (configure and make only read it); the cargo target is
    *cloned* into the worktree (``cp -c``, copy-on-write on APFS; ``--reflink=auto`` elsewhere), so
    the worktree's cargo never writes into the source tree's ``target/``. Nothing in the source
    tree is modified. A fresh worktree then configures and builds in a few minutes instead of
    35-60 (the recipe of the workspace's ``wt/`` node worktrees).
    """

    tree: Path
    triple: str

    @property
    def target(self) -> Path:
        """The source tree's cargo target directory."""
        return self.tree / "target"


def find_reuse(tree: Path | None) -> Reuse | None:
    """A :class:`Reuse` of ``tree`` when it holds a built depends (and a cargo target), else ``None``."""
    if tree is None:
        return None
    triples = built_triples(tree)
    if not triples or not (tree / "target").is_dir():
        return None
    return Reuse(Path(tree).resolve(), triples[0])


def _clone_argv(src: Path, dst: Path) -> tuple[str, ...]:
    if platform.system() == "Darwin":
        # BSD cp (GNU coreutils comes first in the build PATH): clonefile(2), copy-on-write on APFS
        return ("/bin/cp", "-cpR", str(src), str(dst))
    return ("cp", "-a", "--reflink=auto", str(src), str(dst))


def build_env(wt: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment of every build step: GNU libtool/coreutils first in ``PATH`` where Homebrew has
    them (macOS: BSD tools break depends and ``zcutil``), ``LIBTOOLIZE=glibtoolize`` when only that
    exists, and ``CARGO_TARGET_DIR`` pinned to the worktree's own ``target/`` (a global shared
    target from a shell profile would make ``src/Makefile`` miss ``librustzcash.a``)."""
    env = dict(os.environ if base is None else base)
    gnubin = [
        d
        for d in (
            "/opt/homebrew/opt/libtool/libexec/gnubin",
            "/opt/homebrew/opt/coreutils/libexec/gnubin",
            "/opt/homebrew/bin",
            "/usr/local/opt/libtool/libexec/gnubin",
            "/usr/local/opt/coreutils/libexec/gnubin",
        )
        if Path(d).is_dir()
    ]
    if gnubin:
        env["PATH"] = os.pathsep.join([*gnubin, env.get("PATH", "")])
    path = env.get("PATH")
    if not shutil.which("libtoolize", path=path) and shutil.which("glibtoolize", path=path):
        env["LIBTOOLIZE"] = "glibtoolize"
    env["CARGO_TARGET_DIR"] = str(wt / "target")
    return env


def plan_build(
    wt: Path, jobs: int, *, configured: bool | None = None, reuse: Reuse | None = None
) -> list[BuildStep]:
    """The commands a build runs in ``wt`` (``configured`` = ``src/Makefile`` exists).

    With ``reuse`` an unconfigured worktree clones the cargo target, runs ``autogen.sh`` and
    ``configure`` against the borrowed depends prefix instead of ``zcutil/build.sh``.
    """
    if configured is None:
        configured = (wt / "src" / "Makefile").exists()
    steps = []
    if not configured and reuse is not None:
        if not (wt / "target").exists():
            steps.append(BuildStep(_clone_argv(reuse.target, wt / "target"), wt, "clone the cargo target"))
        site = f"$PWD/depends/{reuse.triple}/share/config.site"
        steps.append(BuildStep(("./autogen.sh",), wt, "autogen"))
        configure = f'CONFIG_SITE="{site}" ./configure --quiet'
        steps.append(BuildStep(("sh", "-c", configure), wt, "configure (borrowed depends)"))
    elif not configured:
        steps.append(
            BuildStep(
                ("./zcutil/build.sh", f"-j{jobs}"), wt, "depends + configure + full build (35-60 min cold)"
            )
        )
    steps.append(
        BuildStep(("sh", "-c", CXXBRIDGE_CMD.replace("{jobs}", str(jobs))), wt, "cxx bridge headers")
    )
    steps.append(
        BuildStep(("make", "-C", "src", f"-j{jobs}", "ycashd", "ycash-cli"), wt, "incremental node build")
    )
    return steps


@dataclass
class BuildResult:
    """A finished (or cached) build."""

    binary: BinaryInfo
    worktree: Path
    patch: str
    cached: bool
    log: Path | None = None


def build(
    repo: str | Path,
    sp: OverlaySplit,
    *,
    commit: str = PINNED_COMMIT,
    jobs: int | None = None,
    base: Path | None = None,
    run: Runner = subprocess.run,
    preflight_fn: Callable[..., Ready | Skipped] = preflight,
    force: bool = False,
    reuse_from: Path | Literal["auto"] | None = "auto",
) -> BuildResult | Skipped:
    """Build (or reuse) the ycashd for overlay ``sp`` at ``commit``.

    ``reuse_from`` names a built ycash6 tree whose depends prefix and cargo target the worktree
    borrows (:class:`Reuse`); ``"auto"`` (default) uses ``repo`` itself when it is built, ``None``
    always runs the cold ``zcutil/build.sh``.

    Returns :class:`Skipped` when :func:`preflight` says the environment cannot build. Raises on a
    failed command (the log is under ``.work/logs/``).
    """
    root = base or work_dir()
    full = resolve_commit(repo, commit)
    key = build_key(sp, full)
    if not force:
        hit = cached_binary(key, root)
        if hit:
            return BuildResult(
                hit, Path(hit.manifest.get("worktree", "")), hit.manifest.get("patch", ""), True
            )
    existing = worktree_path(full, root)
    reuse = find_reuse(Path(repo) if reuse_from == "auto" else reuse_from)
    # decide before creating anything; a borrowed depends prefix needs no download host
    pre = preflight_fn(existing if existing.exists() else (reuse.tree if reuse else None))
    if isinstance(pre, Skipped):
        return pre
    wt: Worktree = create_worktree(repo, full, base=root)
    if reuse is not None and not (wt.path / "src" / "Makefile").exists():
        link = wt.path / "depends" / reuse.triple
        if not link.exists() and not link.is_symlink():
            link.symlink_to(reuse.tree / "depends" / reuse.triple)
    # Each overlay starts from the pristine file (a path restore inside the worktree; no branch moves).
    subprocess.run(["git", "-C", str(wt.path), "checkout", "--", PARAMS_CPP], check=True, capture_output=True)
    from ybcal.devnet.overlay import read_params_cpp

    patch = make_patch(read_params_cpp(repo, full), sp.compiled, tag=key)
    apply_patch(wt.path, patch)
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"build-{key}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    jobs = jobs or os.cpu_count() or 2
    env = build_env(wt.path)
    with log.open("w") as fh:
        for step in plan_build(wt.path, jobs, reuse=reuse):
            fh.write(f"$ {' '.join(step.argv)}   # {step.what}\n")
            fh.flush()
            proc = run(list(step.argv), cwd=step.cwd, stdout=fh, stderr=subprocess.STDOUT, env=env)
            if proc.returncode != 0:
                raise RuntimeError(f"build step failed ({step.what}); see {log}")
    out = bin_dir(key, root)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("ycashd", "ycash-cli"):
        src = wt.path / "src" / name
        if src.exists():
            shutil.copy2(src, out / name)
            (out / name).chmod(0o755)
    manifest = {
        "origin": "build",
        "commit": full,
        "key": key,
        "compiled": sp.compiled,
        "runtime": sp.runtime,
        "patch": patch,
        "worktree": str(wt.path),
        "reuse": None if reuse is None else {"tree": str(reuse.tree), "triple": reuse.triple},
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n")
    cli = out / "ycash-cli"
    info = BinaryInfo(out / "ycashd", cli if cli.exists() else None, "build", full, key, manifest)
    return BuildResult(info, wt.path, patch, False, log)


# ---------------------------------------------------------------------------------------------------
# CI artifacts


def default_artifact(system: str | None = None, machine: str | None = None) -> str:
    """``release-<platform>`` for this machine (the yellowback-release.yml matrix names)."""
    s = (system or platform.system()).lower()
    m = (machine or platform.machine()).lower()
    arch = "aarch64" if m in ("arm64", "aarch64") else "x86_64"
    osname = "macos" if s == "darwin" else "linux"
    return f"release-{osname}_{arch}"


def _safe_extract_tar(tar: Path, dest: Path) -> None:
    with tarfile.open(tar) as tf:
        for m in tf.getmembers():
            target = (dest / m.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise RuntimeError(f"unsafe path in {tar.name}: {m.name}")
        tf.extractall(dest)


def _safe_extract_zip(zf_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zf_path) as zf:
        for name in zf.namelist():
            if not str((dest / name).resolve()).startswith(str(dest.resolve())):
                raise RuntimeError(f"unsafe path in {zf_path.name}: {name}")
        zf.extractall(dest)


def install_artifact_dir(src: Path, dest: Path, manifest: Mapping[str, Any]) -> BinaryInfo:
    """Unpack every tarball/zip under ``src``, find ``ycashd``/``ycash-cli``, copy them into ``dest``
    (``chmod 755``, macOS quarantine removed best-effort) and write ``manifest.json``."""
    for arc in list(src.rglob("*.tar.gz")) + list(src.rglob("*.tgz")):
        _safe_extract_tar(arc, arc.parent)
    for arc in src.rglob("*.zip"):
        _safe_extract_zip(arc, arc.parent)
    found: dict[str, Path] = {}
    for p in src.rglob("*"):
        if p.is_file() and p.name in ("ycashd", "ycash-cli") and p.name not in found:
            found[p.name] = p
    if "ycashd" not in found:
        raise RuntimeError(f"no ycashd inside the artifact (looked under {src})")
    dest.mkdir(parents=True, exist_ok=True)
    for name, p in found.items():
        shutil.copy2(p, dest / name)
        (dest / name).chmod(0o755)
        if platform.system() == "Darwin":  # pragma: no cover - macOS only
            subprocess.run(["xattr", "-d", "com.apple.quarantine", str(dest / name)], capture_output=True)
    (dest / "manifest.json").write_text(json.dumps(dict(manifest), indent=2) + "\n")
    cli = dest / "ycash-cli"
    return BinaryInfo(
        dest / "ycashd",
        cli if cli.exists() else None,
        "ci",
        manifest.get("commit"),
        dest.name,
        dict(manifest),
    )


def fetch_ci_binary(
    run_id: str,
    artifact: str | None = None,
    *,
    repo: str = CI_REPO,
    base: Path | None = None,
    gh: str = "gh",
    run: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> BinaryInfo | Skipped:
    """Download a release artifact of CI run ``run_id`` into ``.work/bin/ci-<sha12>/``.

    Uses ``gh run download``; falls back to ``gh api …/artifacts/<id>/zip``. Returns
    :class:`Skipped` when ``gh`` is missing or both downloads fail (e.g. blocked blob storage).
    """
    artifact = artifact or default_artifact()
    if not which(gh):
        return Skipped(f"`{gh}` (GitHub CLI) not found; install it and `gh auth login`", "fetch-binary")
    view = run(
        [gh, "run", "view", str(run_id), "-R", repo, "--json", "headSha,status,conclusion"],
        capture_output=True,
        text=True,
    )
    if view.returncode != 0:
        return Skipped(f"gh run view {run_id} failed: {(view.stderr or '').strip()[:300]}", "fetch-binary")
    meta = json.loads(view.stdout or "{}")
    sha = str(meta.get("headSha") or "")
    if not sha:
        return Skipped(f"run {run_id} reports no headSha", "fetch-binary")
    dest = bin_dir(f"ci-{sha[:12]}", base)
    if (dest / "ycashd").is_file():
        hit = cached_binary(dest.name, base)
        if hit:
            return hit
    manifest = {
        "origin": "ci",
        "repo": repo,
        "run": str(run_id),
        "artifact": artifact,
        "commit": sha,
        "key": dest.name,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    root = base or work_dir()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(root), prefix="fetch-") as tmp:
        tmpd = Path(tmp)
        dl = run(
            [gh, "run", "download", str(run_id), "-R", repo, "-n", artifact, "-D", str(tmpd)],
            capture_output=True,
            text=True,
        )
        if dl.returncode != 0:
            errors = [f"gh run download: {(dl.stderr or '').strip()[:300]}"]
            lst = run(
                [gh, "api", f"repos/{repo}/actions/runs/{run_id}/artifacts"], capture_output=True, text=True
            )
            art_id = None
            if lst.returncode == 0:
                for a in json.loads(lst.stdout or "{}").get("artifacts", []):
                    if a.get("name") == artifact:
                        art_id = a.get("id")
            if art_id is None:
                errors.append(f"artifact {artifact!r} not listed for run {run_id}")
                return Skipped("; ".join(errors), "fetch-binary")
            z = run([gh, "api", f"repos/{repo}/actions/artifacts/{art_id}/zip"], capture_output=True)
            if z.returncode != 0 or not z.stdout:
                err = (
                    z.stderr.decode(errors="replace") if isinstance(z.stderr, bytes) else str(z.stderr or "")
                )
                errors.append(f"gh api …/artifacts/{art_id}/zip: {err.strip()[:300]}")
                return Skipped("; ".join(errors), "fetch-binary")
            zp = tmpd / f"{artifact}.zip"
            zp.write_bytes(z.stdout if isinstance(z.stdout, bytes) else z.stdout.encode())
            _safe_extract_zip(zp, tmpd)
        return install_artifact_dir(tmpd, dest, manifest)

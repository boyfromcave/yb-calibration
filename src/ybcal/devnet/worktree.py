"""Throwaway ycash6 worktree at the pinned commit under ``.work/`` (PLAN §6.2, §2.6).

Owner: WP-9.

The only write this module ever makes to the ycash6 clone is ``git worktree add --detach`` (and
``git worktree remove`` / ``prune`` for the same path): no branch is created, checked out or
moved, nothing is fetched, nothing is committed. A worktree lives at
``<work>/ycash6-<commit12>`` where ``<work>`` is ``$YBCAL_WORK`` or ``<yb-calibration>/.work``;
a path outside the work directory is refused.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

OWNER_WP = "WP-9"

#: The yb-calibration checkout this module lives in (src/ybcal/devnet/worktree.py → repo root).
PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]


class WorktreeError(RuntimeError):
    """A worktree could not be created or removed (or the request was refused)."""


@dataclass(frozen=True)
class Worktree:
    """A detached worktree of the ycash6 clone."""

    repo: Path  #: the ycash6 clone it belongs to
    path: Path  #: the worktree directory (under the work dir)
    commit: str  #: full commit hash checked out (detached)

    @property
    def short(self) -> str:
        """12-character commit prefix used in the directory name."""
        return self.commit[:12]


def work_dir() -> Path:
    """``$YBCAL_WORK`` or ``<yb-calibration>/.work`` (created on demand by callers)."""
    env = os.environ.get("YBCAL_WORK")
    return Path(env).expanduser().resolve() if env else PROJECT_ROOT / ".work"


def ycash6_repo(path: str | Path | None = None) -> Path | None:
    """The ycash6 clone to use: ``path``, else ``$YBCAL_YCASH6``; ``None`` when neither is a git repo."""
    for cand in (path, os.environ.get("YBCAL_YCASH6")):
        if cand and (Path(cand).expanduser() / ".git").exists():
            return Path(cand).expanduser().resolve()
    return None


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def resolve_commit(repo: str | Path, commit: str) -> str:
    """Full hash of ``commit`` if the clone already has it; :class:`WorktreeError` otherwise.

    Never fetches: a missing commit is the owner's to fetch deliberately.
    """
    proc = _git(Path(repo), "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise WorktreeError(
            f"commit {commit!r} is not present in {repo}; fetch it yourself (ybcal never fetches)"
        )
    return proc.stdout.strip()


def is_ancestor(repo: str | Path, older: str, newer: str) -> bool:
    """``git merge-base --is-ancestor older newer`` (both must be present)."""
    return _git(Path(repo), "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def worktree_path(commit: str, base: Path | None = None) -> Path:
    """Where the worktree of ``commit`` (full hash) lives."""
    return (base or work_dir()) / f"ycash6-{commit[:12]}"


def list_worktrees(repo: str | Path) -> dict[Path, str]:
    """Registered worktrees of ``repo``: path → HEAD commit (``""`` when unknown)."""
    out: dict[Path, str] = {}
    cur: Path | None = None
    for line in _git(Path(repo), "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            cur = Path(line[len("worktree ") :]).resolve()
            out[cur] = ""
        elif line.startswith("HEAD ") and cur is not None:
            out[cur] = line[len("HEAD ") :].strip()
    return out


def create_worktree(
    repo: str | Path, commit: str, *, base: Path | None = None, path: Path | None = None
) -> Worktree:
    """Create (or reuse) a detached worktree of ``commit`` under the work dir.

    Refuses when the commit is not in the clone, when ``path`` lies outside the work dir, or when
    the path exists but is not a worktree of that commit.
    """
    repo = Path(repo).expanduser().resolve()
    if not (repo / ".git").exists():
        raise WorktreeError(f"{repo} is not a git clone")
    full = resolve_commit(repo, commit)
    root = (base or work_dir()).resolve()
    target = (path or worktree_path(full, root)).resolve()
    if not _inside(target, root):
        raise WorktreeError(f"refusing to create a worktree outside the work dir {root}: {target}")
    registered = list_worktrees(repo)
    if target in registered:
        if registered[target] != full:
            raise WorktreeError(f"{target} is a worktree at {registered[target][:12]}, not {full[:12]}")
        return Worktree(repo, target, full)
    if target.exists() and any(target.iterdir()):
        raise WorktreeError(f"{target} exists and is not a registered worktree; remove it first")
    root.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "--detach", str(target), full)
    return Worktree(repo, target, full)


def remove_worktree(wt: Worktree | Path, repo: str | Path | None = None) -> None:
    """``git worktree remove --force`` the worktree, then ``git worktree prune``.

    Only paths under the work dir are removed.
    """
    if isinstance(wt, Worktree):
        repo, path = wt.repo, wt.path
    else:
        if repo is None:
            raise WorktreeError("remove_worktree(path) needs the repo")
        path = Path(wt)
    repo = Path(repo).resolve()
    if not _inside(path, work_dir()) and not _inside(path, PROJECT_ROOT):
        raise WorktreeError(f"refusing to remove a worktree outside the work dir: {path}")
    if path.resolve() in list_worktrees(repo):
        _git(repo, "worktree", "remove", "--force", str(path))
    _git(repo, "worktree", "prune")


@contextmanager
def temp_worktree(
    repo: str | Path, commit: str, *, base: Path | None = None, name: str | None = None
) -> Iterator[Worktree]:
    """A worktree that is removed (and pruned) on exit — for patch checks and tests."""
    full = resolve_commit(repo, commit)
    root = (base or work_dir()).resolve()
    path = root / (name or f"tmp-ycash6-{full[:12]}-{os.getpid()}")
    wt = create_worktree(repo, full, base=root, path=path)
    try:
        yield wt
    finally:
        remove_worktree(wt)

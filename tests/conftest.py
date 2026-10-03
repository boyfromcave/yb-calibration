"""Shared fixtures: the extraction at the pin, live from a ycash6 clone when present."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ybcal.params.extract import Extracted, extract, load_snapshot
from ybcal.params.registry import PINNED_COMMIT

REPO_ROOT = Path(__file__).resolve().parents[1]


def ycash6_path() -> Path | None:
    """A usable ycash6 clone ($YBCAL_YCASH6 or /home/user/ycash6); none if $YBCAL_NO_YCASH6 is set."""
    if os.environ.get("YBCAL_NO_YCASH6"):
        return None
    for cand in (os.environ.get("YBCAL_YCASH6"), "/home/user/ycash6"):
        if cand and (Path(cand) / ".git").exists():
            return Path(cand)
    return None


@pytest.fixture(scope="session")
def live_extracted() -> Extracted:
    """Extraction from a real ycash6 clone at the pin; skips when no clone is present (CI)."""
    repo = ycash6_path()
    if repo is None:
        pytest.skip("no ycash6 clone (set YBCAL_YCASH6)")
    try:
        return extract(repo, PINNED_COMMIT)
    except Exception as e:  # pragma: no cover - clone without the pin
        pytest.skip(f"ycash6 clone lacks {PINNED_COMMIT}: {e}")


@pytest.fixture(scope="session")
def extracted() -> Extracted:
    """Live extraction when possible, else the committed snapshot."""
    repo = ycash6_path()
    if repo is not None:
        try:
            return extract(repo, PINNED_COMMIT)
        except Exception:  # pragma: no cover
            pass
    return load_snapshot()

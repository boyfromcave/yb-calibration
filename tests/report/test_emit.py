"""params/emit.py (WP-8): recommended.json and the params.cpp patch."""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from tests.conftest import ycash6_path
from ybcal.params import emit
from ybcal.params.extract import Extracted
from ybcal.params.paramset import ParamSet, mainnet
from ybcal.params.registry import PINNED_COMMIT


def changed_set() -> ParamSet:
    return mainnet().replace(
        {
            "pFastWindow": 48,
            "classMax[0]": 104832,
            "classMin[1]": 104833,
            "bondMin": 30_000 * 10**8,
            "qLowBps": 3500,
            "attestInterval": 12,
            "valveBlocks": 8,
            "DEFAULT_REF_LAG": 3,
            "startHeight": 3_100_000,
            "enforceUntilHeight": 3_520_480,
            "attestRequired": False,
        }
    )


def hunk_lines(diff: str) -> list[int]:
    """Old-file line numbers of changed (-) lines."""
    out, old = [], 0
    for ln in diff.splitlines():
        m = re.match(r"@@ -(\d+)", ln)
        if m:
            old = int(m.group(1))
            continue
        if ln.startswith(("---", "+++", "diff ")):
            continue
        if ln.startswith("-"):
            out.append(old)
            old += 1
        elif ln.startswith(" "):
            old += 1
    return out


def test_vendored_source_is_the_pin():
    src = emit.vendored_params_cpp()
    assert src.commit.startswith(PINNED_COMMIT) and "void SetCommon(Params& p)" in src.text
    repo = ycash6_path()
    if repo is None:
        pytest.skip("no ycash6 clone")
    live = emit.params_cpp_source(repo)
    assert live.text == src.text and live.sha256 == src.sha256


def test_patch_touches_only_setcommon_and_mainparams_with_section_comments():
    base = mainnet()
    res = emit.make_patch(changed_set(), base, sections={"pFastWindow": "§3.1", "pFastMinFill": "§3.1"})
    lines = emit.vendored_params_cpp().text.splitlines(keepends=True)
    spans = emit._function_spans(lines)
    allowed = [range(a + 1, b + 2) for a, b in spans.values()]
    for n in hunk_lines(res.locked) + hunk_lines(res.patch_release):
        assert any(n in r for r in allowed), n
    assert "p.pFastWindow = 48;   p.pFastMinFill = 24;" in res.locked
    assert "(report §3.1)" in res.locked
    assert "p.bondMin             = 30000 * COIN;" in res.locked
    assert "p.qHighBps            = 6500;" in res.locked  # derived follows its parent
    assert "p.attestMaxAge        = 24;" in res.locked  # derived from attestInterval (locked)
    assert "p.attestRequired      = false;" in res.locked
    assert "m.startHeight = 3100000;" in res.locked
    assert "valveBlocks" not in res.locked and "p.valveBlocks         = 8;" in res.patch_release
    assert "p.attestInterval      = 12;" in res.patch_release  # excluded parent → patch-release file
    assert [h["param"] for h in res.header_changes] == ["DEFAULT_REF_LAG"]
    assert not res.unpatched
    every = {c.param for c in res.changes}
    assert {
        "pFastWindow",
        "pFastMinFill",
        "classMax[0]",
        "classMin[1]",
        "bondMin",
        "qLowBps",
        "qHighBps",
        "attestMaxAge",
        "startHeight",
        "enforceUntilHeight",
        "attestRequired",
    } == every


def test_unchanged_set_gives_empty_patch():
    res = emit.make_patch(mainnet(), mainnet())
    assert res.locked == "" and res.patch_release == "" and not res.changes
    assert emit.check_patch("").status == "empty"


def test_patch_applies_in_a_temporary_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("YBCAL_WORK", str(tmp_path))
    repo = ycash6_path()
    if repo is None:
        pytest.skip("no ycash6 clone")
    before = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True
    ).stdout
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout
    res = emit.make_patch(changed_set(), mainnet())
    chk = emit.check_patch(res.locked, repo)
    assert chk.status == "applies", chk.detail
    assert emit.check_patch(res.patch_release, repo).status == "applies"
    bad = res.locked.replace("p.qLowBps             = 3333;", "p.qLowBps             = 1;")
    assert emit.check_patch(bad, repo).status == "fails"
    after = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True
    ).stdout
    assert before == after  # removed and pruned
    assert (
        head
        == subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout
    )


def test_recommended_json_has_the_extract_shape(tmp_path):
    rec = changed_set()
    p = emit.write_recommended(tmp_path / "recommended.json", rec, base=mainnet(), extra={"seed": 1})
    d = json.loads(p.read_text())
    ex = Extracted.from_dict(d)
    back = ParamSet.from_extracted(ex, "main")
    assert back == rec.replace({"network": "main"})
    assert ex.networks["test"]["pFastWindow"] == 48 and ex.networks["test"]["startHeight"] == 0
    assert ex.networks["regtest"]["pFastWindow"] == 8
    assert ex.constants["DEFAULT_REF_LAG"] == 3
    assert (
        d["ybcal"]["changes"]["pFastWindow"] == {"current": 96, "recommended": 48} and d["ybcal"]["seed"] == 1
    )

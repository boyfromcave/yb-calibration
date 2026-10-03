"""The report (WP-8): end to end with stub studies (fast) and with the merged studies (slow)."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from tests.conftest import ycash6_path
from tests.report.toys import TINY, CheapRisk, StubStudy, stub_loader
from ybcal import cli
from ybcal.config import Policy, RunManifest
from ybcal.params.extract import Extracted
from ybcal.params.paramset import ParamSet, mainnet
from ybcal.params.registry import REGISTRY
from ybcal.report import explain as X
from ybcal.report.build import RecommendConfig, run_recommend, section_numbers, tunable_params

STUBS = {
    "G1": StubStudy("G1", {"pFastWindow": -1}, notes=("The early supply cap closes B/C.",)),
    "G2": StubStudy("G2", provenance="synthetic"),
    "G3": StubStudy("G3", {"baseRatioBps[1]": 1}, notes=("the early supply cap closes b/c",)),
    "G4": StubStudy(
        "G4",
        notes=(
            {
                "id": "G4-DN1",
                "title": "Runbook margin",
                "finding": "Abandonment margin is thin.",
                "evidence": {"margin_blocks": 9792},
                "consequence": "Little slack.",
                "fix": "Lengthen.",
                "params": ["abandonBlocks"],
            },
        ),
    ),
    "G5": "broken",
    "G6": StubStudy("G6", {"feeBps": 1}),
    "G8": StubStudy("G8", {"qLowBps": 1}),
    "G9": StubStudy("G9", {"walletConfirmations": 1, "minMint": 1}, blocked=("minMint",)),
}  # G7 and R missing


def run(out: Path, spec=None, *, seed: int | None = None, groups=None) -> object:
    cfg = RecommendConfig(
        budget=TINY,
        policy=Policy(),
        out=out,
        workers=1,
        seed=seed,
        groups=groups,
        ycash6=str(ycash6_path()) if ycash6_path() else None,
    )
    return run_recommend(
        cfg, loader=stub_loader(spec or STUBS), sensitivity_fn=CheapRisk(), on_event=lambda m: None
    )


@pytest.fixture(scope="module")
def stub_report(tmp_path_factory):
    out = tmp_path_factory.mktemp("rep") / "run"
    return run(out), out


def summary_params(md: str) -> list[str]:
    sect = md.split("## 1. Executive summary", 1)[1].split("## 2.", 1)[0]
    return re.findall(r"^\| \[§3\.\d+\]\(#[^)]+\) \| `([^`]+)` \|", sect, flags=re.M)


def test_files_written(stub_report):
    res, out = stub_report
    for f in (
        "report.md",
        "report.html",
        "recommended.json",
        "params.cpp.patch",
        "manifest.json",
        "params-patch-release.patch",
        "evidence/summary.csv",
        "evidence/joint.json",
        "evidence/sensitivity/indices.csv",
        "evidence/sensitivity/tornado.csv",
    ):
        assert (out / f).exists(), f
    assert any((out / "evidence").rglob("*.png"))
    m = RunManifest.load(out / "manifest.json")
    assert m.budget == "quick" and m.extra["counts"] == res.counts
    assert m.extra["recommended_digest"] == res.joint.recommended.digest()


def test_every_tunable_param_once_in_summary_and_others_in_appendix(stub_report):
    _, out = stub_report
    md = (out / "report.md").read_text()
    names = summary_params(md)
    tun = tunable_params()
    assert sorted(names) == sorted(tun) and len(names) == len(set(names))
    appendix = md.split("## Appendix A", 1)[1].split("## Appendix B", 1)[0]
    for k in REGISTRY:
        if k not in tun:
            assert f"| `{k}` |" in appendix, k
    rows = list(csv.DictReader((out / "evidence" / "summary.csv").open()))
    assert [r["param"] for r in rows] == tun


def test_missing_and_broken_studies_are_not_run(stub_report):
    res, out = stub_report
    md = (out / "report.md").read_text()
    assert res.joint.outcomes["G7"].status == "not-run"
    assert res.joint.outcomes["G5"].status == "error"
    n_g5_g7 = sum(1 for p in tunable_params() if REGISTRY[p].group in ("G5", "G7"))
    assert res.counts["NOT RUN"] == n_g5_g7
    assert "| [§3." in md and "NOT RUN" in md and "not run (" in md.lower()
    assert "Studies not run" in md


def test_html_is_self_contained(stub_report):
    _, out = stub_report
    html = (out / "report.html").read_text()
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html)
    assert "<link" not in html and "<script" not in html and "@import" not in html
    assert not re.search(r"url\(\s*['\"]?(https?:)?//", html)
    imgs = re.findall(r'<img [^>]*src="([^"]+)"', html)
    assert imgs and all(s.startswith("data:image/png;base64,") for s in imgs)
    assert "prefers-color-scheme: dark" in html


def test_verdict_counts_and_changes(stub_report):
    res, _ = stub_report
    recs = res.joint.recommendations
    assert recs["pFastWindow"].recommended == 48 and recs["pFastWindow"].verdict == "CHANGE"
    assert recs["sigmaRefBps"].verdict == "PROVISIONAL"
    assert sum(res.counts.values()) == len(tunable_params())
    assert len(res.joint.design_notes) == 2
    dn = res.joint.design_notes[1]
    assert dn.id == "G4-DN1" and dn.title == "Runbook margin" and "abandonBlocks" in dn.params
    assert recs["minMint"].verdict == "BLOCKED"


def test_blocked_and_rich_design_notes_rendered(stub_report):
    _, out = stub_report
    md = (out / "report.md").read_text()
    summ = md.split("## 1. Executive summary", 1)[1].split("**Top remaining risks**", 1)[0]
    assert (
        "**BLOCKED" in summ
        and "`minMint`" in summ
        and "max_bad_debt_prob[B]" in summ
        and "pbad.B 0.031" in summ
    )
    dn = md.split("## 5. Design notes", 1)[1].split("## 6.", 1)[0]
    for part in (
        "Runbook margin",
        "G4-DN1",
        "**Finding:** Abandonment margin is thin.",
        "margin_blocks: 9792",
        "**Consequence:** Little slack.",
        "Lengthen.",
    ):
        assert part in dn, part
    assert "BLOCKED: least-violating value" in (out / "params.cpp.patch").read_text()
    html = (out / "report.html").read_text()
    assert 'class="blocked-box"' in html and "Runbook margin" in html


def test_recommended_json_and_patch(stub_report):
    res, out = stub_report
    d = json.loads((out / "recommended.json").read_text())
    ps = ParamSet.from_extracted(Extracted.from_dict(d), "main")
    assert ps == res.joint.recommended.replace({"network": "main"})
    patch = (out / "params.cpp.patch").read_text()
    nums = section_numbers()
    assert f"pFastWindow 96 -> 48 (report {nums['pFastWindow']})" in patch
    assert "walletConfirmations" not in patch
    assert "walletConfirmations" in (out / "params-patch-release.patch").read_text()
    m = json.loads((out / "manifest.json").read_text())
    if ycash6_path() is not None:
        assert m["extra"]["patch"]["check"]["status"] == "applies"
        assert m["extra"]["patch"]["check_patch_release"]["status"] == "applies"
    else:
        assert m["extra"]["patch"]["check"]["status"] == "skipped"


def test_lock_readiness_and_sections(stub_report):
    _, out = stub_report
    md = (out / "report.md").read_text()
    chk = md.split("## 7. Lock-readiness checklist", 1)[1].split("## Appendix A", 1)[0]
    assert "Lock-ready: **no**" in chk and "The release study passes | FAIL" in chk
    sec = md.split("`pFastWindow` — CHANGE", 1)[1].split("####", 1)[0]
    for part in (
        "What it controls",
        "Decision rule",
        "Binding constraint",
        "Sensitivity",
        "Why not the neighbours",
        "Locked:",
        "| loss |",
        "Figure",
    ):
        assert part in sec, part
    assert "## 4. Joint sensitivity" in md and "Insensitive parameters" in md
    assert "## 5. Design notes" in md and "## 6. Devnet validation" in md


def test_determinism_per_seed(tmp_path):
    spec = {"G1": STUBS["G1"], "G3": STUBS["G3"]}
    a = run(tmp_path / "a", spec, seed=11, groups=["G1", "G3"])
    b = run(tmp_path / "b", spec, seed=11, groups=["G1", "G3"])
    ja = json.loads((tmp_path / "a" / "recommended.json").read_text())
    jb = json.loads((tmp_path / "b" / "recommended.json").read_text())
    assert ja == jb
    assert (tmp_path / "a" / "evidence" / "summary.csv").read_text() == (
        tmp_path / "b" / "evidence" / "summary.csv"
    ).read_text()
    assert a.sensitivity.to_dict()["indices"] == b.sensitivity.to_dict()["indices"]


def test_fmt_value_units():
    assert X.fmt_value("grace", 34560) == "34,560 blocks (30 d)"
    assert X.fmt_value("feeBps", 25) == "25 bps (0.25 %)"
    assert X.fmt_value("feeMin", 50_000_000) == "50,000,000 zat (0.5 YEC)"
    assert X.fmt_value("minMint", 10000) == "$100.00"
    assert X.fmt_value("attestRequired", True) == "true"


# ---------------------------------------------------------------------------------------------------
# CLI


@pytest.fixture
def cheap(monkeypatch):
    import ybcal.optimize.joint as J

    monkeypatch.setattr(J, "load_study", stub_loader(STUBS))
    monkeypatch.setattr(J, "TopRiskModel", CheapRisk)


def test_cli_recommend_and_open(tmp_path, cheap, capsys, monkeypatch):
    import ybcal.report.cli as RC
    from ybcal.studies import base as B

    monkeypatch.setitem(B.BUDGETS, "quick", TINY)
    out = tmp_path / "r"
    assert (
        cli.main(["recommend", "--synthetic", "--out", str(out), "--workers", "1", "--groups", "G1,G3"]) == 0
    )
    o = capsys.readouterr().out
    assert "report:" in o and "verdicts:" in o
    assert cli.main(["report", "open", str(out)]) == 0
    assert capsys.readouterr().out.strip().endswith("report.html")
    assert cli.main(["report", "open", str(tmp_path / "nope")]) == 1
    assert Path("data") / "local" == RC.DEFAULT_LOCAL_DATA
    # reproduce from the manifest
    out2 = tmp_path / "r2"
    assert (
        cli.main(
            ["recommend", "--manifest", str(out / "manifest.json"), "--out", str(out2), "--workers", "1"]
        )
        == 0
    )
    assert json.loads((out / "recommended.json").read_text()) == json.loads(
        (out2 / "recommended.json").read_text()
    )


def test_cli_study_writes_a_mini_report(tmp_path, cheap, capsys, monkeypatch):
    from ybcal.studies import base as B

    monkeypatch.setitem(B.BUDGETS, "quick", TINY)
    out = tmp_path / "s"
    assert cli.main(["study", "G1", "--synthetic", "--out", str(out), "--workers", "1"]) == 0
    md = (out / "report.md").read_text()
    assert summary_params(md) == [p for p in tunable_params() if REGISTRY[p].group == "G1"]
    assert "Yellowback study G1" in md


def test_cli_sensitivity(tmp_path, cheap, capsys, monkeypatch):
    from ybcal.studies import base as B

    monkeypatch.setitem(B.BUDGETS, "quick", TINY)
    out = tmp_path / "sens"
    rc = cli.main(
        [
            "sensitivity",
            "--method",
            "morris",
            "--params",
            "pFastWindow,baseRatioBps[1],nSlots",
            "--synthetic",
            "--out",
            str(out),
            "--workers",
            "1",
        ]
    )
    assert rc == 0
    txt = capsys.readouterr().out
    assert "morris" in txt and "insensitive" in txt and "nSlots" in txt
    d = json.loads((out / "sensitivity.json").read_text())
    assert d["method"] == "morris" and d["per_param"]["nSlots"]["insensitive"]
    assert cli.main(["sensitivity", "--params", "pFastMinFill", "--synthetic"]) == 2


# ---------------------------------------------------------------------------------------------------
# With the merged studies (G1, G2, G5, G8) at a tiny budget


@pytest.mark.slow
def test_end_to_end_with_merged_studies(tmp_path):
    spec = {
        "G1": "real",
        "G2": "real",
        "G5": "real",
        "G8": "real",
        "G3": StubStudy("G3"),
        "G4": StubStudy(
            "G4",
            notes=(
                {
                    "id": "G4-DN1",
                    "title": "Runbook margin",
                    "finding": "Abandonment margin is thin.",
                    "evidence": {"margin_blocks": 9792},
                    "consequence": "Little slack.",
                    "fix": "Lengthen.",
                    "params": ["abandonBlocks"],
                },
            ),
        ),
        "G9": "missing",
    }
    cfg = RecommendConfig(
        budget=TINY,
        policy=Policy(),
        out=tmp_path / "e2e",
        workers=2,
        max_rounds=2,
        ycash6=str(ycash6_path()) if ycash6_path() else None,
    )
    res = run_recommend(cfg, loader=stub_loader(spec), sensitivity_fn=CheapRisk(), on_event=lambda m: None)
    for g in ("G1", "G2", "G5", "G8"):
        assert res.joint.outcomes[g].status == "ok", res.joint.outcomes[g].reason
    md = (tmp_path / "e2e" / "report.md").read_text()
    assert sorted(summary_params(md)) == sorted(tunable_params())
    assert res.counts["NOT RUN"] == sum(
        1 for p in tunable_params() if REGISTRY[p].group in ("G6", "G7", "G9")
    )
    for p in ("pFastWindow", "sigmaRefBps", "signalWindow", "qLowBps"):
        assert res.joint.recommendations[p].explanation
    html = (tmp_path / "e2e" / "report.html").read_text()
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html)
    assert mainnet().check() == []

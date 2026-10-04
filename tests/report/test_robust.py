"""The robustness harness (D-RD-INF-5): runs, resume, tabulation and flags."""

from __future__ import annotations

import json
from pathlib import Path

from tests.report.toys import TINY, CheapRisk, StubStudy, stub_loader
from ybcal import cli
from ybcal.config import Policy
from ybcal.report import robust as R
from ybcal.report.build import RecommendConfig, run_recommend


def fake_runner(calls: list):
    """Runs a stub recommend in-process: feeBps moves only in window 'last365'; peerMin moves on odd
    seeds (seed noise)."""

    def run(cmd, run_dir: Path, nice: int) -> int:
        calls.append(list(cmd))
        seed = int(cmd[cmd.index("--seed") + 1])
        window = cmd[cmd.index("--window") + 1] if "--window" in cmd else "full"
        targets = {"feeBps": 1} if window == "last365" else {}
        if seed % 2:
            targets["peerMin"] = 1
        spec = {"G6": StubStudy("G6", targets)}
        cfg = RecommendConfig(budget=TINY, policy=Policy(owner_pinned={}), out=run_dir, workers=1,
                              groups=["G6"], seed=seed, sensitivity=False)
        run_recommend(cfg, loader=stub_loader(spec), sensitivity_fn=CheapRisk(), on_event=lambda m: None)
        return 0

    return run


def config(tmp_path) -> R.RobustConfig:
    return R.RobustConfig(out=tmp_path / "rob", seeds=[10, 11], windows=["full", "last365"],
                          models=["bootstrap", "regime"], groups="G6")


def test_run_resume_and_tabulate(tmp_path):
    cfg = config(tmp_path)
    calls: list = []
    st = R.run_all(cfg, runner=fake_runner(calls), say=lambda m: None)
    assert len(calls) == 8 and all(s["status"] == "ok" for s in st)
    st2 = R.run_all(cfg, runner=fake_runner(calls), say=lambda m: None)
    assert len(calls) == 8 and all(s["status"] == "cached" for s in st2)  # resumable
    res = R.tabulate(cfg)
    by = {s["param"]: s for s in res["summary"]}
    fee = by["feeBps"]
    assert fee["agreement"] == 0.5 and "unstable" in fee["flags"] and "window-sensitive" in fee["flags"]
    assert fee["by_window"]["full"] == 25 and fee["by_window"]["last365"] > 25
    assert "model-sensitive" not in fee["flags"]
    assert "seed-noise" in by["peerMin"]["flags"]
    assert by["payeeWindow"]["flags"] == [] and by["payeeWindow"]["agreement"] == 1.0
    md = (cfg.out / "robust.md").read_text()
    assert "| `feeBps` | G6 |" in md and "**unstable**" in md
    rows = (cfg.out / "robust.csv").read_text().splitlines()
    assert rows[0].startswith("run,window,model,seed,param")
    assert json.loads((cfg.out / "robust.json").read_text())["missing"] == []


def test_model_sets_and_commands(tmp_path):
    assert R.model_sets("regime+martingale") == ["real_price_model='regime'", "price_drift='martingale'"]
    cfg = config(tmp_path)
    cmd = R.recommend_cmd(cfg, R.RunSpec("2021-22", "martingale", 5), tmp_path / "r")
    assert cmd[cmd.index("--window") + 1] == "2021-22" and "price_drift='martingale'" in cmd
    assert "--no-sensitivity" in cmd and cmd[cmd.index("--groups") + 1] == "G6"
    try:
        R.model_sets("nope")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError


def test_cli_dry_run(capsys, tmp_path):
    rc = cli.main(["robust", "--out", str(tmp_path / "o"), "--dry-run", "--seeds", "2", "--windows",
                   "full,2025-26", "--models", "bootstrap"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 4 and all("recommend" in ln for ln in out)


def test_consolidate_rule():
    per = [("a", {"1": [], "2": []}), ("b", {"1": ["x"], "2": []}), ("c", {"2": [], "3": []}), ("d", None)]
    c = R.consolidate("feeBps", 1, per)
    assert c["value"] == 2 and c["k"] == 3 and c["n"] == 4 and c["violations"] == {}
    c = R.consolidate("feeBps", 3, [("a", {"1": [], "3": []}), ("b", {"1": [], "3": []})])
    assert c["value"] == 3  # tie → closest to current
    c = R.consolidate("feeBps", 1, [("a", {"1": ["x"], "2": ["y"]})])
    assert c["value"] == 1 and c["k"] == 0 and c["violations"] == {"a": ["x"]}


def test_feasible_values_respect_fraction_pins(tmp_path):
    """A row with signalWindow 2016 but thresholds at another window's fraction is not a candidate."""
    rd = tmp_path / "run"
    (rd / "evidence" / "g5").mkdir(parents=True)
    (rd / "manifest.json").write_text(json.dumps({"seed": 1, "policy_path": "", "extra": {}}))
    cols = ["signalWindow", "activationThreshold", "participationFloor", "enforcementFloor",
            "enforcementResume", "feasible", "violated"]
    rows = [
        [2016, 1944, 1556, 1296, 1556, 1, ""],  # inconsistent: thresholds of 2592 → excluded
        [2016, 1512, 1210, 1008, 1210, 0, "false_halt"],
        [2592, 1944, 1556, 1296, 1556, 1, ""],
    ]
    with (rd / "evidence" / "g5" / "results.csv").open("w") as fh:
        fh.write(",".join(cols) + "\n" + "\n".join(",".join(map(str, r)) for r in rows) + "\n")
    rec = {"recommended": 2592, "metrics": {"constraints_current": {"false_halt": False}}}
    recs = {"signalWindow": rec, **{t: {"recommended": v, "metrics": {}} for t, v in
                                    zip(cols[1:5], (1944, 1556, 1296, 1556), strict=True)}}
    fv = R.feasible_values(rd, recs)["signalWindow"]
    assert fv == {"2016": ["false_halt"], "2592": []}
